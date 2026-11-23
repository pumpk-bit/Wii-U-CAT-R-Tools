# scripts/Build.ps1 sets CAFE_ROOT (and the other variables the SDK makefiles need).
ifeq ($(strip $(CAFE_ROOT)),)
$(error Build with scripts/Build.ps1, or set CAFE_ROOT to the Cafe SDK in /c/... form)
endif
MODULE_DEPTH := $(CAFE_ROOT)/system/src
BUILD_MAKE_PATH := $(MODULE_DEPTH)/build/make
MODULE_PATH_FROM_SRC := spark_cube

# gx2spark.a provides GX2DebugCaptureInit and the SPARK capture library.
# nn_util is already in COMMON_LIBNAMES (listing it here is a make error).
MODULE_LIBNAMES := gx2spark

include $(BUILD_MAKE_PATH)/commondefs.mk
include $(BUILD_MAKE_PATH)/commondefs.cos.mk

BIN_DIR := build/$(BUILD_TARGET)
BIN_DIR_NO_TARGET := build
OBJ_DIR := build/obj/$(BUILD_TARGET)

BINNAMES := spark_cube
CSRCS := source/main.c source/hio_socket.c source/dbg_try.c

include $(BUILD_MAKE_PATH)/modulerules.mk

# Nintendo's stock conversion filter expects Cygwin's /cygdrive/c/x form, while
# MSYS2 uses /c/x. Convert the latter (any drive) for the Windows GHS tools, where
# a path starts: at the start of a word, or right after an option such as -L.
override PATH_CYGWIN_TO_DOS = sed 's_\(^\|[^A-Za-z0-9._/-]\|-[A-Za-z]\)/\([A-Za-z]\)/_\1\U\2:/_g'

$(OBJ_DIR)/source/main.o: source/spark_cube_gsh.h

# HIO over sockets (source/hio_socket.c): scripts/Build.ps1 writes
# commsManager_hio.o, gx2spark's commsManager.o with its HIO imports renamed
# to HSK*. Linked ahead of gx2spark.a, so the stock member is never pulled.
COMMS_HIO_O := $(OBJ_DIR)/gx2spark/commsManager_hio.o

$(BIN_DIR)/spark_cube$(BINSUFFIX): $(OBJ_DIR)/source/main.o $(OBJ_DIR)/source/hio_socket.o $(OBJ_DIR)/source/dbg_try.o $(COMMS_HIO_O) $(LIBS)
