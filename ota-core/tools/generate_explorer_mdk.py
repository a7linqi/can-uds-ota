"""Create optional ATK reference-pins, read-only Keil bring-up projects.

Use locally installed ST/CMSIS packs; do not copy vendor libraries into this
project. Existing unconfigured bootloader/app projects are left unchanged.
"""
import argparse
import copy
import os
from pathlib import Path
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
HAL_SOURCES = ["stm32f4xx_hal" + suffix + ".c" for suffix in
               ("", "_can", "_cortex", "_dma", "_flash", "_flash_ex", "_gpio",
                "_i2c", "_iwdg", "_pwr", "_pwr_ex", "_rcc", "_rcc_ex", "_spi", "_uart")]


def node(parent, name, value=""):
    child = ET.SubElement(parent, name)
    child.text = str(value)
    return child


def relative(path, parent):
    try:
        return os.path.relpath(path, parent).replace("/", "\\")
    except ValueError:  # Packs may be installed on a different Windows drive.
        return str(path).replace("/", "\\")


def generate(dfp, cmsis):
    dfp, cmsis = dfp.resolve(), cmsis.resolve()
    hal = dfp / "Drivers/STM32F4xx_HAL_Driver"
    device = dfp / "Drivers/CMSIS/Device/ST/STM32F4xx"
    vendor = [hal / "Src" / name for name in HAL_SOURCES]
    vendor += [device / "Source/Templates/system_stm32f4xx.c"]
    for path in vendor + [hal / "Inc/stm32f4xx_hal.h", device / "Include/stm32f407xx.h",
                          cmsis / "core_cm4.h"]:
        if not path.is_file():
            raise ValueError(f"Required ST/CMSIS pack file is missing: {path}")
    project = ET.Element("Project")
    node(project, "SchemaVersion", "2.1")
    node(project, "Header", "ATK Explorer common reference pins / READ-ONLY BRING-UP")
    targets = node(project, "Targets")
    board = ROOT / "platform/stm32f407/board/explorer_reference"
    includes = [board, ROOT / "include", ROOT / "platform/stm32f407/include",
                ROOT / "platform/stm32f407/examples", ROOT / "third_party/ed25519",
                hal / "Inc", device / "Include", cmsis]
    mdk = ROOT / "mdk"
    output = mdk / "explorer_readonly.uvprojx"
    mdk.mkdir(exist_ok=True)
    core = sorted((ROOT / "src/core").glob("*.c"))
    adapter = [p for p in sorted((ROOT / "platform/stm32f407/src").glob("*.c"))
               if p.name != "system_minimal.c"]
    ed25519 = sorted((ROOT / "third_party/ed25519").glob("*.c"))
    for part, capacity in (("ZG", 0x100000), ("ZE", 0x80000)):
        for role, loopback in (("loopback", 1), ("diagnostics", 0)):
            name = f"explorer_{part}_{role}_READONLY"
            target = node(targets, "Target")
            for key, value in (("TargetName", name), ("ToolsetNumber", "0x4"),
                               ("ToolsetName", "ARM-ADS"), ("uAC6", "1"),
                               ("pCCUsed", "6070000::V6.7::ARMCLANG")):
                node(target, key, value)
            options = copy.deepcopy(ET.parse(ROOT / "tools/templates/target_option.xml").getroot())
            target.append(options)
            for element in options.iter():
                if element.text:
                    element.text = element.text.replace("{DEVICE}", f"STM32F407{part}Tx")
            common = options.find("TargetCommonOption")
            values = {
                "Device": f"STM32F407{part}Tx", "PackID": "Keil.STM32F4xx_DFP.2.16.0",
                "Cpu": 'IROM(0x08000000,0x10000) IRAM(0x20000000,0x20000) CPUTYPE("Cortex-M4") ELITTLE',
                "OutputDirectory": f"..\\build\\mdk_{name}\\", "OutputName": name,
                "ListingPath": f"..\\build\\mdk_{name}\\", "CreateExecutable": "1",
                "CreateHexFile": "0", "FlashDriverDll": "", "StartupFile": "",
                "DebugInformation": "1", "BrowseInformation": "0", "SelectedForBatchBuild": "0",
            }
            for key, value in values.items():
                element = common.find(key)
                if element is None:
                    element = node(common, key)
                element.text = value
            ads = options.find("TargetArmAds")
            misc = ads.find("ArmAdsMisc")
            for key, value in (("AdsCpuType", '"Cortex-M4"'), ("useUlib", "1"),
                               ("hadIROM", "1"), ("hadIRAM", "1"), ("hadIRAM2", "0")):
                misc.find(key).text = value
            memory = misc.find("OnChipMemories")
            for key, address, size in (("IROM", 0x08000000, 0x10000),
                                       ("IRAM", 0x20000000, 0x20000),
                                       ("OCR_RVCT4", 0x08000000, 0x10000),
                                       ("OCR_RVCT9", 0x20000000, 0x20000)):
                memory.find(key + "/StartAddress").text = hex(address)
                memory.find(key + "/Size").text = hex(size)
            cads = ads.find("Cads")
            cads.find("Optim").text = "2"; cads.find("uC99").text = "1"; cads.find("v6Lang").text = "1"
            controls = cads.find("VariousControls")
            controls.find("MiscControls").text = "-std=c99 -Wall -Wextra -mfloat-abi=soft"
            controls.find("Define").text = (
                "STM32F407xx,USE_HAL_DRIVER,__MICROLIB,"
                f"FLASH_TOTAL_SIZE={capacity}u,HSE_VALUE=8000000u,"
                f"OTA_EXPLORER_REFERENCE_PINS=1,OTA_EXPLORER_CAN_LOOPBACK={loopback},"
                "OTA_ENABLE_FLASHING=0,OTA_ENABLE_BOOT_HANDOFF=0,OTA_ENABLE_WATCHDOG=0")
            controls.find("IncludePath").text = ";".join(relative(p, mdk) for p in includes)
            aads = ads.find("Aads")
            aads.find("ClangAsOpt").text = "0"
            aads.find("VariousControls/Define").text = "__MICROLIB"
            linker = ads.find("LDads")
            for key, value in (("umfTarg", "0"), ("useFile", "1"), ("noStLib", "0"),
                               ("ScatterFile", f"..\\firmware\\linker\\bootloader_{part}.sct"),
                               ("TextAddressRange", "0x08000000"), ("DataAddressRange", "0x20000000"),
                               ("Misc", "--entry Reset_Handler --strict")):
                element = linker.find(key)
                if element is None:
                    element = node(linker, key)
                element.text = value
            groups = node(target, "Groups")
            sources = {
                "Portable core": core,
                "STM32 adapters": adapter,
                "Ed25519 (vendored)": ed25519,
                "Read-only Explorer BSP": [board / "explorer_reference.c", board / "explorer_interrupts.c",
                                           ROOT / "platform/stm32f407/examples/board_hal_binding.c"],
                "Firmware entry": [ROOT / ("firmware/explorer_loopback/main.c" if loopback
                                           else "firmware/bootloader/main.c")],
                "Installed ST HAL and SystemInit": vendor,
                "ST startup": [ROOT / "third_party/st/startup_stm32f407xx.s"],
            }
            for group_name, files in sources.items():
                group = node(groups, "Group"); node(group, "GroupName", group_name)
                group_files = node(group, "Files")
                for path in files:
                    item = node(group_files, "File")
                    node(item, "FileName", path.name)
                    node(item, "FileType", "2" if path.suffix == ".s" else "1")
                    node(item, "FilePath", relative(path, mdk))
    ET.indent(project, space="  ")
    ET.ElementTree(project).write(output, encoding="utf-8", xml_declaration=True)
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dfp", type=Path, required=True)
    parser.add_argument("--cmsis", type=Path, required=True)
    args = parser.parse_args()
    try:
        output = generate(args.dfp, args.cmsis)
    except (ValueError, OSError) as error:
        parser.error(str(error))
    print(f"Generated {output}; 4 read-only reference-pins targets, no hardware accessed.")


if __name__ == "__main__":
    main()
