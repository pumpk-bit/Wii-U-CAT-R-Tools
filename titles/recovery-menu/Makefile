# scripts/Build.ps1 sets CAFE_ROOT (and the other variables the SDK makefiles need).
ifeq ($(strip $(CAFE_ROOT)),)
$(error Build with scripts/Build.ps1, or set CAFE_ROOT to the Cafe SDK in /c/... form)
endif
MODULE_DEPTH := $(CAFE_ROOT)/system/src
BUILD_MAKE_PATH := $(MODULE_DEPTH)/build/make
MODULE_PATH_FROM_SRC := cat_recovery_menu

include $(BUILD_MAKE_PATH)/commondefs.mk
include $(BUILD_MAKE_PATH)/commondefs.cos.mk

BIN_DIR := build/$(BUILD_TARGET)
BIN_DIR_NO_TARGET := build
OBJ_DIR := build/obj/$(BUILD_TARGET)

BINNAMES := cat_recovery_menu
CSRCS := source/main.c source/titles.c source/ui.c source/menulog.c

include $(BUILD_MAKE_PATH)/modulerules.mk

# Nintendo's stock conversion filter expects Cygwin's /cygdrive/c/x form, while
# MSYS2 uses /c/x. Convert the latter (any drive) for the Windows GHS tools, where
# a path starts: at the start of a word, or right after an option such as -L.
override PATH_CYGWIN_TO_DOS = sed 's_\(^\|[^A-Za-z0-9._/-]\|-[A-Za-z]\)/\([A-Za-z]\)/_\1\U\2:/_g'

$(BIN_DIR)/cat_recovery_menu$(BINSUFFIX): $(OBJ_DIR)/source/main.o $(OBJ_DIR)/source/titles.o $(OBJ_DIR)/source/ui.o $(OBJ_DIR)/source/menulog.o $(LIBS)
