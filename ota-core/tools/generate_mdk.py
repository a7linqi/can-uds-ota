"""Generate STUB-only Keil projects. Does not choose board pins or clocks."""
from pathlib import Path
import xml.etree.ElementTree as ET
import copy

ROOT = Path(__file__).resolve().parents[1]

def node(parent, tag, value=""):
    child = ET.SubElement(parent, tag)
    child.text = str(value)
    return child

def main():
    mdk = ROOT / "mdk"
    linker = ROOT / "firmware/linker"
    mdk.mkdir(exist_ok=True)
    linker.mkdir(parents=True, exist_ok=True)
    for image in ("bootloader", "app"):
        project = ET.Element("Project")
        node(project, "SchemaVersion", "2.1")
        node(project, "Header", "CAN UDS STM32F407 preparation / UNCONFIGURED BOARD")
        targets = node(project, "Targets")
        for part, capacity in (("ZG", 0x100000), ("ZE", 0x80000)):
            base, length = (0x08000000, 0x10000) if image == "bootloader" else (0x08010000, capacity-0x10000)
            name = f"{image}_{part}_UNCONFIGURED"
            scatter = linker / f"{image}_{part}.sct"
            scatter.write_text(
                f"; Generated STUB layout: {name}\n"
                f"LR_IROM1 0x{base:08X} 0x{length:X} {{\n"
                f"  ER_IROM1 0x{base:08X} 0x{length:X} {{\n"
                "    startup_stm32f407xx.o (RESET, +First)\n"
                "    *(InRoot$$Sections)\n"
                "    .ANY (+RO)\n"
                "  }\n"
                "  RW_IRAM1 0x20000000 0x20000 { .ANY (+RW +ZI) }\n"
                "}\n", encoding="utf-8")
            target = node(targets, "Target")
            for tag, value in (("TargetName", name), ("ToolsetNumber", "0x4"),
                               ("ToolsetName", "ARM-ADS"), ("pCCUsed", "6070000::V6.7::ARMCLANG"),
                               ("uAC6", "1")):
                node(target, tag, value)
            options = copy.deepcopy(ET.parse(ROOT / "tools/templates/target_option.xml").getroot())
            target.append(options)
            for element in options.iter():
                if element.text:
                    element.text = element.text.replace("{DEVICE}", f"STM32F407{part}Tx")
            common = options.find("TargetCommonOption")
            values = {
                "Device": f"STM32F407{part}Tx", "Vendor": "STMicroelectronics",
                "PackID": "Keil.STM32F4xx_DFP.2.16.0",
                "Cpu": f'IROM(0x{base:X},0x{length:X}) IRAM(0x20000000,0x20000) CPUTYPE("Cortex-M4") ELITTLE',
                "OutputDirectory": f"..\\build\\mdk_{name}\\", "OutputName": name,
                "CreateExecutable": "1", "CreateLib": "0", "CreateHexFile": "0",
                "DebugInformation": "1", "BrowseInformation": "0",
                "ListingPath": f"..\\build\\mdk_{name}\\", "SelectedForBatchBuild": "1",
                "FlashDriverDll": "", "StartupFile": "",
            }
            for tag, value in values.items():
                child = common.find(tag)
                if child is None: child = node(common, tag)
                child.text = value
            ads = options.find("TargetArmAds")
            misc = ads.find("ArmAdsMisc")
            for tag, value in (("AdsCpuType", '"Cortex-M4"'), ("useUlib", "1"),
                               ("hadIROM", "1"), ("hadIRAM", "1"), ("hadIRAM2", "0"),
                               ("AdsLmap", "1"), ("AdsLsym", "1"), ("AdsLszi", "1")):
                child = misc.find(tag)
                if child is None: child = node(misc, tag)
                child.text = value
            memories = misc.find("OnChipMemories")
            for key, address, size in (("IROM", base, length), ("IRAM", 0x20000000, 0x20000)):
                memory = memories.find(key)
                memory.find("Type").text = "1" if key == "IROM" else "0"
                memory.find("StartAddress").text = hex(address)
                memory.find("Size").text = hex(size)
            for key, address, size in (("OCR_RVCT4", base, length), ("OCR_RVCT9", 0x20000000, 0x20000)):
                memories.find(key + "/StartAddress").text = hex(address)
                memories.find(key + "/Size").text = hex(size)
            cads = ads.find("Cads")
            for tag, value in (("Optim", "2"), ("uC99", "1"), ("v6Lang", "1"), ("wLevel", "3")):
                cads.find(tag).text = value
            controls = cads.find("VariousControls")
            controls.find("MiscControls").text = "-std=c99 -Wall -Wextra -mfloat-abi=soft"
            controls.find("Define").text = f"STM32F407xx,__MICROLIB,FLASH_TOTAL_SIZE={capacity}u,OTA_ENABLE_FLASHING=0,OTA_ENABLE_BOOT_HANDOFF=0,OTA_ENABLE_WATCHDOG=0"
            controls.find("IncludePath").text = "..\\include;..\\platform\\stm32f407\\include;..\\third_party\\ed25519"
            aads = ads.find("Aads")
            aads.find("ClangAsOpt").text = "0"  # ST ARMASM startup
            ac = aads.find("VariousControls")
            ac.find("Define").text = "__MICROLIB"
            ld = ads.find("LDads")
            for tag, value in (("umfTarg", "0"), ("useFile", "1"), ("noStLib", "0"),
                               ("ScatterFile", f"..\\firmware\\linker\\{scatter.name}"),
                               ("TextAddressRange", hex(base)), ("DataAddressRange", "0x20000000"),
                               ("Misc", "--entry Reset_Handler --strict")):
                child = ld.find(tag)
                if child is None: child = node(ld, tag)
                child.text = value
            groups = node(target, "Groups")
            sources = {
                "Portable core": sorted((ROOT / "src/core").glob("*.c")),
                "Optional chip driver": sorted((ROOT / "src/driver").glob("*.c")),
                "STM32 adapter": sorted((ROOT / "platform/stm32f407/src").glob("*.c")),
                "Ed25519 (vendored)": sorted((ROOT / "third_party/ed25519").glob("*.c")),
                "UNCONFIGURED board": [ROOT / "platform/stm32f407/board/board_unconfigured.c"],
                image: [ROOT / f"firmware/{image}/main.c"],
                "ST startup": [ROOT / "third_party/st/startup_stm32f407xx.s"],
            }
            for group_name, paths in sources.items():
                group = node(groups, "Group")
                node(group, "GroupName", group_name)
                items = node(group, "Files")
                for path in paths:
                    item = node(items, "File")
                    node(item, "FileName", path.name)
                    node(item, "FileType", "2" if path.suffix == ".s" else "1")
                    node(item, "FilePath", "..\\" + str(path.relative_to(ROOT)).replace("/", "\\"))
        ET.indent(project, space="  ")
        ET.ElementTree(project).write(mdk / f"{image}.uvprojx", encoding="utf-8", xml_declaration=True)
    print("Generated bootloader/app projects and four scatter files; board remains unconfigured.")

if __name__ == "__main__":
    main()
