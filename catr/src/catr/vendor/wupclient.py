# may or may not be inspired by plutoo's ctrrpc
from argparse import ArgumentParser
import binascii
import errno
import os
import socket
import struct
import sys
import time

def buffer(size):
    return bytearray([0x00] * size)

def copy_string(buffer, s, offset):
    s += "\0"
    buffer[offset : (offset + len(s))] = bytearray(s, "ascii")

def copy_word(buffer, w, offset):
    buffer[offset : (offset + 4)] = struct.pack(">I", w)

def copy_u64(buffer, w, offset):
    buffer[offset : (offset + 8)] = struct.pack(">Q", w)

def get_string(buffer, offset):
    s = buffer[offset:]
    if 0x00 in s:
        return s[:s.index(0x00)].decode("utf-8")
    else:
        return s.decode("utf-8")

class wupclient:
    s=None

    def __init__(self, ip, port=1337, timeout=3.0):
        self.fsa_handle = None
        self.cwd = "/vol/storage_mlc01"
        self.s = socket.create_connection((ip, port), timeout=timeout)
        self.s.settimeout(timeout)
        # Do NOT set TCP_NODELAY. This wupserver handles one recv() as one message, so a
        # write's header and payload must arrive separately. Nagle's wait for the ACK is what
        # keeps them apart. With NODELAY (or one combined send) the server timed out on
        # 2026-11-14.

    def __del__(self):
        # Explicit wrappers close device handles while connected. Never do RPC in GC.
        if getattr(self, 's', None):
            self.s.close()

    def _recv_exact(self, length):
        result = bytearray()
        while len(result) < length:
            part = self.s.recv(length - len(result))
            if not part:
                raise ConnectionError("wupserver disconnected mid-response")
            result.extend(part)
        return bytes(result)

    def send(self, command, data):
        self.s.sendall(struct.pack('>I', command) + data)
        response = self._recv_exact(8 if command == 2 else 4)
        return struct.unpack('>I', response[:4])[0], response[4:]

    # core commands

    def read(self, addr, length):
        self.s.sendall(struct.pack('>III', 1, addr, length))
        data = self._recv_exact(length)
        ret = struct.unpack('>I', self._recv_exact(4))[0]
        if ret:
            raise OSError(f"wupserver read failed: {ret:08x}")
        return data

    def write(self, addr, data):
        if not data:
            return 0
        # Header and payload must go in separate sends: this wupserver reads the 12-byte
        # header on its own; one combined send timed out on 2026-11-14.
        self.s.sendall(struct.pack('>III', 0, addr, len(data)))
        self.s.sendall(data)
        ret = struct.unpack('>I', self._recv_exact(4))[0]
        if ret:
            raise OSError(f"wupserver write failed: {ret:08x}")
        return 0

    def svc(self, svc_id, arguments):
        data = struct.pack(">I", svc_id)
        for a in arguments:
            data += struct.pack(">I", a)
        ret, data = self.send(2, data)
        if ret == 0:
            return struct.unpack(">I", data)[0]
        else:
            print("svc error : %08X" % ret)
            return None

    def kill(self):
        ret, _ = self.send(3, bytearray())
        return ret

    def memcpy(self, dst, src, len):
        data = struct.pack(">III", dst, src, len)
        ret, data = self.send(4, data)
        if ret == 0:
            return ret
        else:
            print("memcpy error : %08X" % ret)
            return None

    def repeatwrite(self, dst, val, n):
        data = struct.pack(">III", dst, val, n)
        ret, data = self.send(5, data)
        if ret == 0:
            return ret
        else:
            print("repeatwrite error : %08X" % ret)
            return None

    # derivatives
    def alloc(self, size, align = None):
        if size == 0:
            return 0
        if align == None:
            return self.svc(0x27, [0x1, size])
        else:
            return self.svc(0x28, [0x1, size, align])

    def free(self, address):
        if address == 0:
            return 0
        return self.svc(0x29, [0x1, address])

    def load_buffer(self, b, align = None):
        if len(b) == 0:
            return 0
        address = self.alloc(len(b), align)
        self.write(address, b)
        return address

    def load_string(self, s, align = None):
        return self.load_buffer(bytearray(s + "\0", "ascii"), align)

    def open(self, device, mode):
        address = self.load_string(device)
        handle = self.svc(0x33, [address, mode])
        self.free(address)
        return handle

    def close(self, handle):
        return self.svc(0x34, [handle])

    def ioctl(self, handle, cmd, inbuf, outbuf_size):
        in_address = self.load_buffer(inbuf)
        out_data = None
        if outbuf_size > 0:
            out_address = self.alloc(outbuf_size)
            ret = self.svc(0x38, [handle, cmd, in_address, len(inbuf), out_address, outbuf_size])
            out_data = self.read(out_address, outbuf_size)
            self.free(out_address)
        else:
            ret = self.svc(0x38, [handle, cmd, in_address, len(inbuf), 0, 0])
        self.free(in_address)
        return (ret, out_data)
        
    def otpread(self, index, size):
        out_data = None
        out_address = self.alloc(size)
        self.write(out_address, bytearray([0x00] * size))
        ret = self.svc(0x22, [index, out_address, size])
        out_data = self.read(out_address, size)
        self.free(out_address)
        return (ret, out_data)

    def iovec(self, vecs):
        data = bytearray()
        for (a, s) in vecs:
            data += struct.pack(">III", a, s, 0)
        return self.load_buffer(data)

    def ioctlv(self, handle, cmd, inbufs, outbuf_sizes, inbufs_ptr = [], outbufs_ptr = []):
        inbufs = [(self.load_buffer(b, 0x40), len(b)) for b in inbufs]
        outbufs = [(self.alloc(s, 0x40), s) for s in outbuf_sizes]
        iovecs = self.iovec(inbufs + inbufs_ptr + outbufs_ptr + outbufs)
        out_data = []
        ret = self.svc(0x39, [handle, cmd, len(inbufs + inbufs_ptr), len(outbufs + outbufs_ptr), iovecs])
        for (a, s) in outbufs:
            out_data += [self.read(a, s)]
        for (a, _) in (inbufs + outbufs):
            self.free(a)
        self.free(iovecs)
        return (ret, out_data)

    # fsa
    def FSA_Mount(self, handle, device_path, volume_path, flags):
        inbuffer = buffer(0x520)
        copy_string(inbuffer, device_path, 0x0004)
        copy_string(inbuffer, volume_path, 0x0284)
        copy_word(inbuffer, flags, 0x0504)
        (ret, _) = self.ioctlv(handle, 0x01, [inbuffer, bytearray()], [0x293])
        return ret

    def FSA_RawOpen(self, handle, device):
        inbuffer = buffer(0x520)
        copy_string(inbuffer, device, 0x4)
        (ret, data) = self.ioctl(handle, 0x6A, inbuffer, 0x293)
        return (ret, struct.unpack(">I", data[4:8])[0])

    def FSA_FlushVolume(self, handle, device):
        inbuffer = buffer(0x520)
        copy_string(inbuffer, device, 0x4)
        (ret, data) = self.ioctl(handle, 0x1B, inbuffer, 0x293)
        return (ret, struct.unpack(">I", data[4:8])[0])

    def FSA_RawClose(self, handle, file_handle):
        inbuffer = buffer(0x520)
        copy_word(inbuffer, file_handle, 0x4)
        (ret, data) = self.ioctl(handle, 0x6D, inbuffer, 0x293)
        return ret

    def FSA_RawRead(self, handle, device_handle, size, cnt, offset):
        inbuffer = buffer(0x520)
        copy_u64(inbuffer, offset, 0x8)
        copy_word(inbuffer, cnt, 0x10)
        copy_word(inbuffer, size, 0x14)
        copy_word(inbuffer, device_handle, 0x18)
        (ret, data) = self.ioctlv(handle, 0x6B, [inbuffer], [size * cnt, 0x293])
        return (ret, data[0])

    def FSA_RawReadPtr(self, handle, device_handle, size, cnt, offset, ptr):
        inbuffer = buffer(0x520)
        copy_u64(inbuffer, offset, 0x8)
        copy_word(inbuffer, cnt, 0x10)
        copy_word(inbuffer, size, 0x14)
        copy_word(inbuffer, device_handle, 0x18)
        (ret, data) = self.ioctlv(handle, 0x6B, [inbuffer], [0x293], [], [(ptr, size*cnt)])
        return (ret, data[0])

    def FSA_GetDeviceInfo(self, handle, path):
        inbuffer = buffer(0x520)
        copy_string(inbuffer, path, 0x4)
        copy_word(inbuffer, 4, 0x284); #GetDeviceInfo mode
        (ret, data) = self.ioctl(handle, 0x18, inbuffer, 0x293)
        return (ret, data[4:0x2C])

    def FSA_GetVolumeInfo(self, handle, path):
        inbuffer = buffer(0x520)
        copy_string(inbuffer, path, 0x4)
        copy_word(inbuffer, 4, 0x284);
        (ret, data) = self.ioctl(handle, 3, inbuffer, 0x293, 0x1C0)
        return (ret, data[4:0x1C0])
    
    def FSA_OpenDir(self, handle, path):
        inbuffer = buffer(0x520)
        copy_string(inbuffer, path, 0x4)
        (ret, data) = self.ioctl(handle, 0x0A, inbuffer, 0x293)
        return (ret, struct.unpack(">I", data[4:8])[0])

    def FSA_ReadDir(self, handle, dir_handle):
        inbuffer = buffer(0x520)
        copy_word(inbuffer, dir_handle, 0x4)
        (ret, data) = self.ioctl(handle, 0x0B, inbuffer, 0x293)
        data = data[4:]
        unk = data[:0x64]
        if ret == 0:
            return (ret, {"name" : get_string(data, 0x64), "is_file" : (unk[0] & 0x80) != 0x80, "unk" : unk})
        else:
            return (ret, None)

    def FSA_CloseDir(self, handle, dir_handle):
        inbuffer = buffer(0x520)
        copy_word(inbuffer, dir_handle, 0x4)
        (ret, data) = self.ioctl(handle, 0x0D, inbuffer, 0x293)
        return ret

    def FSA_OpenFile(self, handle, path, mode):
        inbuffer = buffer(0x520)
        copy_string(inbuffer, path, 0x4)
        copy_string(inbuffer, mode, 0x284)
        (ret, data) = self.ioctl(handle, 0x0E, inbuffer, 0x293)
        return (ret, struct.unpack(">I", data[4:8])[0])
    
    def FSA_GetStat(self, handle, path):
        inbuffer = buffer(0x520)
        copy_string(inbuffer, path, 0x4)
        copy_word(inbuffer, 0x5, 0x284)
        (ret, data) = self.ioctl(handle, 0x18, inbuffer, 0x293)
        return (ret, data[4:0x68])

    def FSA_Rename(self, handle, src, dst):
        inbuffer = buffer(0x520)
        copy_string(inbuffer, src, 0x4)
        copy_string(inbuffer, dst, 0x284)
        (ret, _) = self.ioctl(handle, 0x09, inbuffer, 0x293)
        return ret
        
    def FSA_Remove(self, handle, path):
        inbuffer = buffer(0x520)
        copy_string(inbuffer, path, 0x4)
        (ret, _) = self.ioctl(handle, 0x08, inbuffer, 0x293)
        return ret

    def FSA_MakeDir(self, handle, path, flags):
        inbuffer = buffer(0x520)
        copy_string(inbuffer, path, 0x4)
        copy_word(inbuffer, flags, 0x284)
        (ret, _) = self.ioctl(handle, 0x07, inbuffer, 0x293)
        return ret

    def FSA_ReadFile(self, handle, file_handle, size, cnt):
        inbuffer = buffer(0x520)
        copy_word(inbuffer, size, 0x08)
        copy_word(inbuffer, cnt, 0x0C)
        copy_word(inbuffer, file_handle, 0x14)
        (ret, data) = self.ioctlv(handle, 0x0F, [inbuffer], [size * cnt, 0x293])
        return (ret, data[0])

    def FSA_WriteFile(self, handle, file_handle, data):
        inbuffer = buffer(0x520)
        copy_word(inbuffer, 1, 0x08) # size
        copy_word(inbuffer, len(data), 0x0C) # cnt
        copy_word(inbuffer, file_handle, 0x14)
        (ret, data) = self.ioctlv(handle, 0x10, [inbuffer, data], [0x293])
        return (ret)

    def FSA_ReadFilePtr(self, handle, file_handle, size, cnt, ptr):
        inbuffer = buffer(0x520)
        copy_word(inbuffer, size, 0x08)
        copy_word(inbuffer, cnt, 0x0C)
        copy_word(inbuffer, file_handle, 0x14)
        (ret, data) = self.ioctlv(handle, 0x0F, [inbuffer], [0x293], [], [(ptr, size*cnt)])
        return (ret, data[0])

    def FSA_WriteFilePtr(self, handle, file_handle, size, cnt, ptr):
        inbuffer = buffer(0x520)
        copy_word(inbuffer, size, 0x08)
        copy_word(inbuffer, cnt, 0x0C)
        copy_word(inbuffer, file_handle, 0x14)
        (ret, data) = self.ioctlv(handle, 0x10, [inbuffer], [0x293], [(ptr, size*cnt)], [])
        return (ret)

    def FSA_CloseFile(self, handle, file_handle):
        inbuffer = buffer(0x520)
        copy_word(inbuffer, file_handle, 0x4)
        (ret, data) = self.ioctl(handle, 0x15, inbuffer, 0x293)
        return ret

    def FSA_ChangeMode(self, handle, path, permission):
        if len(path) > 0x27F:
            print(" Path is too long!");
            return -1;
        inbuffer = buffer(0x520)
        copy_string(inbuffer, path, 4)
        copy_word(inbuffer, permission, 0x284)
        copy_word(inbuffer, 0x666, 0x288)
        (ret, _) = self.ioctl(handle, 0x20, inbuffer, 0x293)
        return ret

    def FSA_MapDir(self, handle, src_dir, dst_dir, flags, arg1, arg2, arg3, arg4):
        inbuffer = buffer(0x520)
        copy_string(inbuffer, src_dir, 4)
        copy_string(inbuffer, dst_dir, 0x284)
        copy_word(inbuffer, flags, 0x504)
        copy_word(inbuffer, arg1, 0x508)
        copy_word(inbuffer, arg2, 0x50C)
        copy_word(inbuffer, arg3, 0x510)
        copy_word(inbuffer, arg4, 0x514)
        copy_word(inbuffer, 0, 0x518)

        (ret, data) = self.ioctlv(handle, 0x67, [inbuffer], [0x293], [(0, 0)])
        return (ret)

    # pm
    def PM_Restart(self, handle):
        (ret, _) = self.ioctl(handle, 0xE3, [], 0)
        return ret

    # mcp
    def MCP_InstallGetInfo(self, handle, path):
        inbuffer = buffer(0x27F)
        copy_string(inbuffer, path, 0x0)
        (ret, data) = self.ioctlv(handle, 0x80, [inbuffer], [0x16])
        return (ret, struct.unpack(">IIIIIH", data[0]))

    # mcp
    def BSP_Read(self, handle, entityName, instance, attributeName, out_size):
        inbuffer = buffer(0x48)
        copy_string(inbuffer, entityName, 0x0)
        copy_word(inbuffer, instance, 0x8*4)
        copy_string(inbuffer, attributeName, 0x9*4)
        copy_word(inbuffer, out_size, 17*4)
        (ret, data) = self.ioctl(handle, 0x05, inbuffer, out_size)
        return (ret, data)

    # mcp
    def BSP_Write(self, handle, entityName, instance, attributeName, data):
        inbuffer = buffer(0x48) + bytearray(data)
        copy_string(inbuffer, entityName, 0x0)
        copy_word(inbuffer, instance, 0x8*4)
        copy_string(inbuffer, attributeName, 0x9*4)
        copy_word(inbuffer, len(data), 17*4)
        (ret, data) = self.ioctl(handle, 0x06, inbuffer, 0)
        return (ret, data)

    def MCP_Install(self, handle, path):
        inbuffer = buffer(0x27F)
        copy_string(inbuffer, path, 0x0)
        (ret, _) = self.ioctlv(handle, 0x81, [inbuffer], [])
        return ret

    def MCP_Delete(self, handle, path, uninstall):
        inbuffer = buffer(0x38)
        copy_string(inbuffer, path, 0x0)
        inbuffer2 = buffer(0x4)
        copy_word(inbuffer2, uninstall, 0)
        (ret, _) = self.ioctlv(handle, 0x83, [inbuffer, inbuffer2], [], [], [])
        return ret

    def MCP_Uninstall(self, handle, path):
        inbuffer = buffer(0x38)
        copy_string(inbuffer, path, 0x0)
        (ret, _) = self.ioctlv(handle, 0x84, [inbuffer], [], [], [])
        return ret

    def MCP_CopyTitle(self, handle, path, dst_device_id, flush):
        inbuffer = buffer(0x27F)
        copy_string(inbuffer, path, 0x0)
        inbuffer2 = buffer(0x4)
        copy_word(inbuffer2, dst_device_id, 0x0)
        inbuffer3 = buffer(0x4)
        copy_word(inbuffer3, flush, 0x0)
        (ret, _) = self.ioctlv(handle, 0x85, [inbuffer, inbuffer2, inbuffer3], [])
        return ret

    def MCP_InstallGetProgress(self, handle):
        (ret, data) = self.ioctl(handle, 0x82, [], 0x24)
        return (ret, struct.unpack(">IIIIIIIII", data))
    
    def MCP_InstallSetTargetDevice(self, handle, device):
        inbuffer = buffer(4)
        copy_word(inbuffer, device, 0)
        (ret, data) = self.ioctl(handle, 0x8D, inbuffer, 0)
        return ret
    
    def MCP_InstallSetTargetUsb(self, handle, device):
        inbuffer = buffer(4)
        copy_word(inbuffer, device, 0)
        (ret, data) = self.ioctl(handle, 0xF1, inbuffer, 0)
        return ret

    def MCP_GetDefaultTitleId(self, handle):
        (ret, data) = self.ioctl(handle, 0x65, [], 0x8)
        return ret, data

    def MCP_SetDefaultTitleId(self, handle, tid):
        inbuffer = buffer(8)
        copy_u64(inbuffer, tid, 0)
        (ret, data) = self.ioctl(handle, 0x62, inbuffer, 0)
        return ret

    # bsp
    def BSPRead(self, handle, device, offset, operation, length):
        inbuffer = buffer(0x48)
        copy_string(inbuffer, device, 0)
        copy_word(inbuffer, offset, 0x20)
        copy_string(inbuffer, operation, 0x24)
        copy_word(inbuffer, length, 0x44)
        (ret, data) = self.ioctl(handle, 0x5, inbuffer, length)
        return ret, data


    # syslog (tmp)
    def dump_syslog(self, pointer_address=None):
        if pointer_address is None:
            raise ValueError("Verified IOSU syslog pointer address required")
        syslog_address = struct.unpack(">I", self.read(pointer_address, 4))[0]
        
        log_size = struct.unpack(">I", self.read(syslog_address, 4))[0]
        print("Dumping log of size " + str(log_size))
        syslog_address += 0x10
        
        data = self.read(syslog_address, log_size)
        f = open("syslog.txt", "wb")
        f.write(data)
        #print(data.decode("ascii")) #print

    # file management
    def get_fsa_handle(self):
        if self.fsa_handle == None:
            self.fsa_handle = self.open("/dev/fsa", 0)
        return self.fsa_handle

    def mkdir(self, path, flags):
        fsa_handle = self.get_fsa_handle()
        ret = self.FSA_MakeDir(fsa_handle, path, flags)
        if ret == 0:
            return 0
        if ret == 0xFFFCFFEA:
            print("mkdir: dir already exists")
            return 0
        else:
            print("mkdir error (%s, %08X)" % (path, ret))
            return ret

    def chmod(self, path, mode):
        if path[0] != "/" and self.cwd[0] == "/":
            return self.chmod(self.cwd + "/" + path, mode)
        fsa_handle = self.get_fsa_handle()
        ret = self.FSA_ChangeMode(fsa_handle, path, mode)
        if ret == 0:
            return 0
        else:
            print("chmod error (%s, %08X)" % (path, ret))
            return ret
        
    def chmod_r(self, path, mode):
        if path[0] != "/" and self.cwd[0] == "/":
            return self.chmod_r(self.cwd + "/" + path)

        self.chmod(path, mode)
        entries = self.ls(path, True)
        for e in entries:
            print(path + "/" + e["name"])
            if e["is_file"]:
                self.chmod(path + "/" + e["name"], mode)
            else:
                self.chmod_r(path + "/" + e["name"], mode)

    def cd(self, path):
        if path[0] != "/" and self.cwd[0] == "/":
            return self.cd(self.cwd + "/" + path)
        fsa_handle = self.get_fsa_handle()
        ret, dir_handle = self.FSA_OpenDir(fsa_handle, path if path != None else self.cwd)
        if ret == 0:
            self.cwd = path
            self.FSA_CloseDir(fsa_handle, dir_handle)
            return 0
        else:
            print("cd error : path does not exist (%s)" % (path))
            return -1

    def rm(self, path):
        fs_handle = self.get_fsa_handle()
        ret = self.FSA_Remove(fs_handle, path)
        if ret == 0:
            return 0
        else:
            print("rm error : " + hex(ret))
            return -1

    def rmdir(self, path):
        entries = self.ls(path, True)
        for e in entries:
            cur_dir = path + "/" + e["name"]
            print(cur_dir)
            if e["is_file"] == False:
                self.rmdir(cur_dir)
            self.rm(cur_dir)
        self.rm(path)

    def ls(self, path = None, return_data = False):
        fsa_handle = self.get_fsa_handle()
        ret, dir_handle = self.FSA_OpenDir(fsa_handle, path if path != None else self.cwd)
        if ret != 0x0:
            print("opendir error : " + hex(ret))
            return [] if return_data else None
        entries = []
        while True:
            ret, data = self.FSA_ReadDir(fsa_handle, dir_handle)
            if ret != 0:
                break
            if not(return_data):
                if data["is_file"]:
                    print("     %s" % data["name"])
                else:
                    print("     %s/" % data["name"])
            else:
                entries += [data]
        ret = self.FSA_CloseDir(fsa_handle, dir_handle)
        return entries if return_data else None

    def dldir(self, path):
        entries = self.ls(path, True)
        for e in entries:
            print(path + "/" + e["name"])
            if e["is_file"]:
                self.dl(path + "/" + e["name"])
            else:
                mkdir_p(e["name"])
                os.chdir(e["name"])
                self.dldir(path + "/" + e["name"])
                os.chdir("..")

    def cpdir(self, srcpath, dstpath):
        entries = self.ls(srcpath, True)
        q = [(srcpath, dstpath, e) for e in entries]
        while len(q) > 0:
            _srcpath, _dstpath, e = q.pop()
            _srcpath += "/" + e["name"]
            _dstpath += "/" + e["name"]
            if e["is_file"]:
                print(e["name"])
                self.cp(_srcpath, _dstpath)
            else:
                self.mkdir(_dstpath, 0x666)
                entries = self.ls(_srcpath, True)
                q += [(_srcpath, _dstpath, e) for e in entries]

    def pwd(self):
        return self.cwd

    def cp(self, filename_in, filename_out):
        fsa_handle = self.get_fsa_handle()
        ret, in_file_handle = self.FSA_OpenFile(fsa_handle, filename_in, "r")
        if ret != 0x0:
            print("cp error : could not open " + filename_in + "with ret" + hex(ret))
            return
        ret, out_file_handle = self.FSA_OpenFile(fsa_handle, filename_out, "w")
        if ret != 0x0:
            print("cp error : could not open " + filename_out + "with ret" + hex(ret))
            return
        block_size = 0x100000
        buffer = self.alloc(block_size, 0x40)
        k = 0
        while True:
            ret, _ = self.FSA_ReadFilePtr(fsa_handle, in_file_handle, 0x1, block_size, buffer)
            k += ret
            ret = self.FSA_WriteFilePtr(fsa_handle, out_file_handle, 0x1, ret, buffer)
            sys.stdout.write(hex(k) + "\r"); sys.stdout.flush();
            if ret < 1:
                break
        self.free(buffer)
        ret = self.FSA_CloseFile(fsa_handle, out_file_handle)
        ret = self.FSA_CloseFile(fsa_handle, in_file_handle)
        self.chmod(filename_out, 0x666)
        self.FSA_FlushVolume(fsa_handle, '/vol/storage_mlc01')
        self.FSA_FlushVolume(fsa_handle, '/vol/storage_usb01')
        self.FSA_FlushVolume(fsa_handle, '/vol/storage_slc01')

    def df(self, filename_out, src, size):
        fsa_handle = self.get_fsa_handle()
        ret, out_file_handle = self.FSA_OpenFile(fsa_handle, filename_out, "w")
        if ret != 0x0:
            print("df error : could not open " + filename_out)
            return
        block_size = 0x10000
        buffer = self.alloc(block_size, 0x40)
        k = 0
        while k < size:
            cur_size = min(size - k, block_size)
            self.memcpy(buffer, src + k, cur_size)
            k += cur_size
            ret = self.FSA_WriteFilePtr(fsa_handle, out_file_handle, 0x1, cur_size, buffer)
            sys.stdout.write(hex(k) + " (%f) " % (float(k * 100) / size) + "\r"); sys.stdout.flush();
        self.free(buffer)
        ret = self.FSA_CloseFile(fsa_handle, out_file_handle)


    def dl(self, filename, local_filename = None):
        fsa_handle = self.get_fsa_handle()
        if filename[0] != "/":
            filename = self.cwd + "/" + filename
        if local_filename == None:
            if "/" in filename:
                local_filename = filename[[i for i, x in enumerate(filename) if x == "/"][-1]+1:]
            else:
                local_filename = filename
        ret, file_handle = self.FSA_OpenFile(fsa_handle, filename, "r")
        if ret != 0x0:
            print("dl error : could not open " + filename)
            return

        # create our remote buffer
        block_size = 0x40000
        chunk_buf = self.alloc(block_size, 0x40)
        if chunk_buf == 0:
            print("failed to alloc chunk buffer!")
            return

        ret = 0
        offs = 0
        f = open(local_filename, "wb")
        while 1:
            offs += block_size
            ret, data = self.FSA_ReadFilePtr(fsa_handle, file_handle, 0x1, block_size, chunk_buf)
            if ret == block_size:
                print(hex(offs))
            
            #read 0 bytes, or error
            if ret < 1:
                break
            
            f.write(self.read(chunk_buf, ret))

        ret = self.FSA_CloseFile(fsa_handle, file_handle)
        f.close()
        self.free(chunk_buf)


    def fr(self, filename, offset, size):
        fsa_handle = self.get_fsa_handle()
        if filename[0] != "/":
            filename = self.cwd + "/" + filename
        ret, file_handle = self.FSA_OpenFile(fsa_handle, filename, "r")
        if ret != 0x0:
            print("fr error : could not open " + filename)
            return
        buffer = bytearray()
        block_size = 0x400
        while True:
            ret, data = self.FSA_ReadFile(fsa_handle, file_handle, 0x1, block_size if (block_size < size) else size)
            buffer += data[:ret]
            sys.stdout.write(hex(len(buffer)) + "\r"); sys.stdout.flush();
            if len(buffer) >= size:
                break
        ret = self.FSA_CloseFile(fsa_handle, file_handle)
        return buffer

    def fw(self, filename, offset, buffer):
        fsa_handle = self.get_fsa_handle()
        if filename[0] != "/":
            filename = self.cwd + "/" + filename
        ret, file_handle = self.FSA_OpenFile(fsa_handle, filename, "r+")
        if ret != 0x0:
            print("fw error : could not open " + filename)
            return
        block_size = 0x400
        k = 0
        while True:
            cur_size = min(len(buffer) - k, block_size)
            if cur_size <= 0:
                break
            sys.stdout.write(hex(k) + "\r"); sys.stdout.flush();
            ret = self.FSA_WriteFile(fsa_handle, file_handle, buffer[k:(k+cur_size)])
            k += cur_size
        ret = self.FSA_CloseFile(fsa_handle, file_handle)

    def up(self, local_filename, filename = None):
        fsa_handle = self.get_fsa_handle()
        if filename == None:
            if "/" in local_filename:
                filename = local_filename[[i for i, x in enumerate(local_filename) if x == "/"][-1]+1:]
            else:
                filename = local_filename
        if filename[0] != "/":
            filename = self.cwd + "/" + filename
        f = open(local_filename, "rb")
        ret, file_handle = self.FSA_OpenFile(fsa_handle, filename, "w")
        if ret != 0x0:
            print("up error : could not open " + filename)
            return
        progress = 0
        block_size = 0x40000
        while True:
            data = f.read(block_size)
            ret = self.FSA_WriteFile(fsa_handle, file_handle, data)
            progress += len(data)
            sys.stdout.write(hex(progress) + "\r"); sys.stdout.flush();
            if len(data) < block_size:
                break
        ret = self.FSA_CloseFile(fsa_handle, file_handle)
