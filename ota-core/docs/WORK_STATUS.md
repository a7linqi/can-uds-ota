# Engineering checkpoint

Latest completed stage: physical Phase 2 ZG silent CAN loopback and read-only storage probe (2026-11-02). Core v0.13 remains frozen; full upgrade/recovery hardware acceptance is still pending. Older checkpoints describe their capture-time state.

Full ownership handover (2026-11-02, Asia/Shanghai):
- The user requested continued work primarily through the other harness due to limited GPT usage. Workspace PROJECT_TAKEOVER.md is now the authoritative project handover and roadmap; HARNESS_TASKS.md points to it.
- The receiving harness becomes the single owner of core, BSP, App, PC tools, testing, hardware execution and delivery. Earlier split-writer restrictions and waits for this ChatGPT maintainer are historical, not continuing requirements. Evidence-based stage prerequisites, backup preservation and partition limits remain in force.
- Frozen v0.13 is a reproducible baseline, not a permanent prohibition on core fixes. The new owner may implement, self-review, test and issue new source-matched builds and versions; it must not manually forge verification hashes or present models as physical acceptance.
- Immediate work is the checked NOR backup handoff, then actual-part qualification/physical CAN, restricted storage writes, production Boot/App health integration, real CAN/UDS update and rollback, fault/power-cut acceptance, delivery and user learning. Missing hardware blocks dependent stages only.
- This handover itself performs no new board operation. Latest physical full-NOR success was not present at the handover inspection; board state is the loopback image as last reported, subject to newer actual logs.
- Newer local evidence during handover supersedes that last board report: the checked host fixes are installed, the read-only backup firmware is running, and the real four-block probe completed at 2026-11-02T23:40:14+08:00. Maintenance independently checked all four raw uploads, matching before/after headers, block CRCs and the 32768-byte output. Full dual-read is now running in backups/nor-original/full/20261102_234049_609858/. Do not restart, reset, rebuild or change firmware while adopting this process. Full-NOR completion is still pending; handover/nor-progress.json records the observation.

Phase 3 R1 review and read-only NOR execution release (2026-11-02, Asia/Shanghai):
- Independently reviewed the revised v2 stream state machine and strict EEPROM input handling. An isolated copy passed the actual C stream test, fake-CLI checks, output isolation and two independent 16 MiB model reads; the actual C EEPROM length matrix passed for 256 bytes and rejected 0/100/255/257/300/4096.
- Found remaining host failure-record and stale-upload problems, plus six stale build input bindings. Maintenance prepared three exact checked replacements outside executor ownership: host/nor_backup.py, package.py and tests/run_checks.py. apply_r1.py checks source hashes and preserves history; the executor, not maintenance, applies them.
- Additional isolated regressions prove fresh upload paths, timeout output retention, reset/session/missing-CLI partial records, a four-block probe marked NOT a complete backup, and invalid time-budget rejection. No hardware subprocess was accessed during these checks.
- Fresh isolated GNU/ST HAL build with current source reproduces exactly the submitted 12288-byte BIN SHA256 af69df8ebf8a27d145d4d98b26cc78589dc44073ddd6d1c1f44831fe03200197; mailbox is 0x20000278, 40-byte header plus 8192 data bytes. This fixed read-only firmware is approved within workspace PHASE3_NOR_BACKUP_RUN.md.
- After applying the checked host fixes and rebinding checks/build, the executor may program only this fixed target, run four real blocks, then perform two complete 16777216-byte NOR reads if the probe passes. Raw data, logs and SHA256 must be retained. No further per-step user approval is required inside that fixed scope.
- Hardware execution and the full NOR backup are still pending; board state remains the loopback image as last reported. No persistent external storage writes, App handoff or real upgrade recovery is authorized by this backup stage. Storage-integration and frozen core source/evidence remain unchanged.

Independent storage integration preparation (2026-11-02, Asia/Shanghai):
- Maintenance implemented stages/storage-integration/ while execution retains board-lab fixes. Core sources remain frozen; this stage uses translation-unit-local symbol renames rather than editing the reference BSP or optional NOR HAL glue.
- The candidate binding requires an explicit full NOR profile, EEPROM 8-byte physical page, preserved original backups and qualified timing. Actual qualification remains unknown. Target writes are locked; the write-enabled variant is restricted to a host model by a compile guard.
- Added bounded EEPROM ACK polling and readback verification before returning durable success, plus an I/O fault latch. HAL internal timeouts still require working SysTick; physical timing and bus-fault acceptance remain pending.
- Actual C model checks passed: 8 locked assertions/1 scenario group and 387 candidate assertions/5 groups. Frozen NOR command driver, actual NOR HAL glue, STM32 ports and Bootloader were exercised together with simulated peripherals/internal Flash.
- Used the actual linked executable V1/V2 signed packages: V1 install/health-confirm model -> V2 internal-program failure -> driver/port context recreation -> complete reinstall -> unconfirmed trial rollback to V1. Packages were staged directly in the model, not transferred over CAN/UDS. No App instructions or physical power cuts were executed.
- GNU linked explorer_ZG_storage_LOCKED: 50 objects, BIN 38472 bytes, MSP 0x20020000, Reset 0x080084A9; storage status symbol 0x2000207C. Flashing/handoff/watchdog/storage-write gates all 0. This target is not approved for programming.
- Core 119-input evidence stayed matched; no full global matrix rerun. Independent stage checks/build and package bind their own sources and inputs. Next physical work still requires the executor's repaired NOR backup and actual chip qualification.

Phase 3 executor submission review (2026-11-02, Asia/Shanghai):
- Accepted the existing device inventory and independently checked all three physical reset/retest logs and raw probe structures: stage=3/passed=16, NOR EF4018 and identical CRC-valid 256-byte EEPROM snapshots. These are resets, not power cuts. No USB-CAN or board USB UART was seen by the submitted enumeration.
- The actual C metadata tool accepts the real snapshot with no writes, but its oversized-input claim is false for the submitted source: a 300-byte file is truncated and accepted. The review executed actual source with TinyCC -run because the submitted EXE cannot launch in this execution environment.
- Requested changes before programming the NOR backup firmware. Independent offline reproduction found a TypeError in the real SWD adapter read path, overwritten BINs with a stale successful manifest on rerun, accepted sequence gaps and an unhandled short header. Source and ARM disassembly expose a READY publication window; an ordering model demonstrates the resulting false CRC failure.
- Workspace PHASE3_REVIEW_FIXES.md assigns R1..R7 fixes to the execution harness, including actual C/fake-CLI checks, coherent mailbox ownership/session handling, immutable outputs and source-bound builds. Review evidence is in stages/board-lab-review/. No execution-side source was edited by maintenance.
- The board remains on the verified Phase 2 loopback according to the executor; full NOR backup and physical CAN remain unexecuted. The submitted 12028-byte NOR backup BIN is NOT approved for programming. Frozen core inputs and historical full verification remain untouched.

Phase 2 acceptance and expanded executor scope (2026-11-02):
- Independently checked the executor's raw program/readback logs, 296-byte probe, EEPROM snapshot, target BIN and both original internal Flash backups. Download/verify/reset succeeded; RAM counters are stage=3 and passed=16. Project loopback firmware has actually run on the board.
- Probe reads DEV_ID 0x413, REV_ID 0x1007, internal Flash 1048576 bytes. HCLK/PCLK1/PCLK2 read 168/42/84 MHz; these are configuration readbacks, not external frequency measurements.
- NOR RDID is EF 40 18, status 0. Full suffix, electrical specifications and write profile remain unconfirmed. EEPROM status is 0; its 256 bytes are exactly 00..FF, CRC32 0x29058C73. Data origin and EEPROM model/page size remain unknown.
- Evidence and acceptance are in workspace stages/app-bringup/evidence/phase2-*. Original program entry was overwritten; the double-read original Flash backup is preserved. External storage writes, App handoff, physical CAN and real upgrade/power-cut recovery are not yet accepted.
- Raw CLI warning shows sn=I was not reliably accepted for serial selection. Future hardware operations must enumerate probes and confirm the actual connected identity.
- All 119 frozen core inputs still match successful verification; no full matrix rerun or historical verification.json rewrite was needed.
- User requested more work for the other harness. Workspace HARNESS_TASKS.md and PHASE3_EXECUTOR_WORK.md delegate independent stages/board-lab/: device/UART inventory, three reset retests, actual-snapshot C metadata checks, read-only full NOR backup firmware/collector, and conditional physical CAN DID acceptance. Maintenance retains write binding and Boot/App recovery integration.

Independent App preparation and original-backup checkpoint (2026-11-02):
- Accepted the actual double-read original internal Flash backup from backups/board-original/20261102_204315_470/: each BIN is 1048576 bytes, SHA256 a919efd8201a2d86b479baeb7ab327a90c28eaefd443b937364f42562b7e367c. Recomputed both files independently; see evidence/hardware/2026-11-02/backup-accepted.json. External NOR/EEPROM are not included.
- Kept all 119 core implementation/build inputs frozen and matching the successful v0.13 evidence. No full core matrix was rerun. New source and independent evidence are outside the core under workspace stages/app-bringup/.
- Built two actual fixed-address App identities from the real firmware/app/main.c and read-only reference BSP. Explicit SystemInit wrapper restores VTOR=0x08010000 after the installed ST initialization, before App main enables IRQs; linker also checks the vector symbol address.
- V1/V2: 46 objects each, linked ELF/BIN/MAP and signed IMG2. BIN sizes 14824 / 14836 bytes, signed packages 14920 / 14932 bytes. Both initial MSP values 0x20020000; reset vectors 0x08013021 / 0x08013029.
- Independent host checks passed: 17 real-entry assertions per App, four forbidden-gate compile failures, ten ELF/BIN layout tests. The actual C image validator accepted each signed executable package and rejected its bad-signature and unsigned versions.
- UART identity and configured heartbeat toggle intervals distinguish V1 (500 ms) and V2 (1000 ms). Health confirmation remains explicitly blocked until real storage/health binding; these are bring-up Apps, not a completed successful-upgrade demonstration.
- Next executor task is PHASE2_BOARD_PROBE.md: preserve the accepted backup, verify the specified ZG loopback BIN hash, program only that bring-up image and return RAM probe/counter data. Prepared status only: no project firmware was programmed or executed by this session.

v0.13 delivery checkpoint (2026-11-02):
- Reproduced S+L signature malleability in both the actual C OTA validator and PC verifier before fixing it. The reproduction is preserved in evidence/security/pre-fix-scalar-reproduction.json. This is an encoding problem, not proof of arbitrary-message forgery.
- Enforced canonical S, bounded signature inputs and the backup-only CRC exception; PC point decoding also rejects noncanonical coordinates and invalid zero-x sign encodings.
- Preserved both sets of signature-path tests already present in the working tree. Added storage-read fault propagation and interrupted-install recovery after package signature damage.
- The full matrix exposed stale unsigned fixtures in the real python-can virtual backend. Positive paths now use signed packages; unsigned replacement of a confirmed image and signature tampering are explicitly rejected through the actual C ECU process.
- Final complete matrix passed in 134.835 s: 119 implementation/build inputs bound by SHA256; 3138 C assertions / 420 model power-cut scenarios per capacity; PC 45; python-can virtual 7; SPI NOR 720; Python packaging/signing 22; eight Python-to-C package acceptance/rejection cases.
- ARMClang reference targets: 43 C objects each. Four GNU targets: 45 compiled/assembled objects each and validated ELF/BIN/MAP. BIN sizes: ZG loopback 12736, ZG diagnostics 38852, ZE loopback 12740, ZE diagnostics 38860 bytes; all fit the 64 KiB boot region. Keil complete link is still unverified.
- See docs/CORE_REVIEW_20261102.md for code and verification scope. Workspace-root HARNESS_TASKS.md delegates read-only original Flash backup and physical evidence collection to the other harness; core source changes should have a single writer during each verification run.
- Full hardware acceptance remains false. Only SWD identification is physically verified. The original internal Flash backup is prepared but has not run; fitted NOR/EEPROM, project firmware execution, physical CAN, flashing and power-cut measurements remain pending.

Signature path audit (2026-11-02):
- Confirmed the signature is actually reached on all three acceptance paths: UDS 0x37 (download), boot_select READY/INSTALLING via pending_ok (install), and boot_select RESTORING via backup_ok (restore). 0x36 intentionally defers verification to 0x37 so a half-transferred package is never judged.
- Fixed three real defects surfaced by tests/test_signature.c. (1) image_signature_verify dereferenced a NULL signature pointer. (2) Only the top three bits of S were checked, so a non-canonical S (S+L) verified under a second encoding; RFC 8032 requires S < L. (3) image_validate_backup accepted DOWNLOAD_OFFSET and headers carrying flags, so the CRC-only backup exemption could have validated a download.
- All arguments are now checked before any flash read: non-NULL signature, offset limited to the two defined containers, 8 <= image_size <= IMAGE_MAX_PAYLOAD, canonical S. image_validate_backup accepts only BACKUP_OFFSET with flags == 0.
- Added tests/test_boot.c signature_paths(): end-to-end UDS cases in which CRC and header checks BOTH pass and only the signature can reject (payload altered with CRCs recomputed, forged version, untrusted key, flipped signature bit, unsigned container). It also proves the backup container is accepted by the CRC-only validator but rejected by the signed one.
- Verification: 3138 checks / 420 power-cut scenarios per capacity (ZG and ZE), 0 failures. PC 45, NOR 720, firmware 35+35+51, adapters 177/243, tools 22. Four GNU read-only images linked (ZG loopback 12736, ZG diagnostics 38852 bytes). evidence/verification.json status=passed, signature_implemented=true, hardware_verified=false.
- Boundary unchanged: backup containers stay CRC-only by design, the demo seed is not a production secret, and no on-target timing or physical flashing was measured.

Physical identification checkpoint (2026-11-02):
- The user ran CHECK_BOARD_READONLY.cmd from the normal Windows desktop account. CubeProgrammer 2.23.0 connected through physical ST-LINK SN I / V2J47S7, SWD Hot Plug, exit code 0.
- Raw DBGMCU_IDCODE 0x10076413: DEV_ID 0x413, REV_ID 0x1007 (tool: Rev 2.0). Flash-size register 0x1FFF7A22 returned 0x0400 = 1024 KiB; tool reported 1 MBytes. Use the ZG capacity configuration; this family ID does not establish the complete MCU marking or PCB revision.
- Reported target reference voltage 3.25 V; requested SWD 1000 kHz, reported 950 kHz. Board power routing and fitted NOR/EEPROM parameters remain unverified.
- Preserved the original identification log and structured facts under evidence/hardware/2026-11-02/. This is physical identification only, not execution of project firmware or full hardware acceptance.
- Prepared BACKUP_BOARD_READONLY.cmd in the workspace root. It reads the whole internal 1 MiB twice into a new directory and accepts a manifest only for equal-length, equal-SHA256 outputs. The backup has NOT been run yet; NOR/EEPROM are outside its scope.
- Next: user launches the read-only backup -> inspect and preserve the original Flash files -> prepare the ZG silent-loopback bring-up stage. No firmware programming was performed in this session; existing flashing/handoff/watchdog gates remain unchanged.

Signature (L4) checkpoint (2026-11-01):
- Added Ed25519 (RFC 8032 / SHA-512) image signature verification. Signed message = 32-byte IMG2 header + payload; 64-byte signature appended; flags bit0 = IMAGE_FLAG_SIGNED. OTA_REQUIRE_SIGNATURE=1 rejects unsigned images.
- Vendored orlp/ed25519 (Zlib) into third_party/ed25519; pure-Python signer in tools/ed25519.py (hashlib only). Public key hardcoded in include/ota/pubkey.h (demo seed, not a production secret).
- Streaming verify (src/core/signature.c): payload is hashed in chunks through flash_port_t, so a 1 MiB image never loads into RAM. Backup containers remain CRC-only (image_validate_backup): no private key at restore time, and the backup already came from a signature-verified install.
- UDS 0x34/0x37 now account for the 64-byte signature in the download size.
- Host regression still 3050 checks / 420 power-cut scenarios per capacity, 0 failures. PC 45, NOR 720, firmware 35+35+51, adapters 177/243, tools 14 — all pass. RFC 8032 vectors + Python-sign→C-verify cross-check pass; tampered/unsigned rejected.
- Four GNU read-only targets linked with ed25519 (diagnostics grew ~22 KiB→~38 KiB, still < 64 KiB boot region); ARMClang 43 objects per target compiled -Werror clean.
- Boundary: verify uses *_vartime ops (acceptable for signature verification, no private key involved); NOT secure boot, no hardware rollback counter, demo seed is not production-grade.

v0.12 checkpoint (2026-10-31):
- User first suspected V2; supplied photo resembles official V3 layout. Physical PCB revision, full MCU marking and fitted NOR part remain unconfirmed.
- Read manufacturer V2 schematic and V3 hardware manual via public mirrors; names/sizes match official listings, but official byte identity is unproven. Relevant diagrams were visually checked.
- Added common reference-pins HAL BSP: 8 MHz HSE / 168 MHz HCLK, PF9/10 LEDs, USART1 PA9/10, CAN1 PA11/12, SPI1 PB3/4/5 with PB14 CS, I2C1 PB8/9.
- Init checks family ID and actual Flash capacity, reads only NOR RDID and 256 EEPROM bytes. Successful reads do not enable storage writes.
- Added silent internal loopback entry and normal-CAN read-only diagnostic entry, each with ZG/ZE targets; programming, handoff and watchdog gates are compile-time blocked.
- Actual loopback entry + actual receive queue model: 51 assertions, 14 scenarios, zero failures. No physical CAN was exercised.
- Four optional ARMClang targets each compiled 35 C objects including installed ST HAL sources.
- Installed a local 64-bit xPack GNU Arm GCC 15.2.1 toolchain; verified its published archive SHA256. No global OS/compiler settings were changed.
- Four GNU targets each compiled/assembled 37 objects and linked. BIN sizes: ZG loopback 12736, ZG diagnostics 22248, ZE loopback 12740, ZE diagnostics 22256 bytes. All stay within boot 0x08000000..0x0800FFFF.
- ELF load segments, initial SP 0x20020000, Thumb reset vector, reset entry and BIN contents were checked. This is not physical execution or a runtime stack measurement.
- GCC strict checks found and fixed two portability issues: image header array parameter declarations and an unsigned comparison against zero in the HAL reference binding.
- Full v0.12 matrix passed in 116.704 seconds. Existing C/PC/NOR/adapter/watchdog/virtual-CAN/packer checks passed unchanged in count.
- Source and linked image hashes are bound to evidence. Package with --include-firmware to ship the verified ELF/BIN/MAP files. Keil ARMASM/armlink and physical acceptance remain pending.
- Next: confirm actual MCU capacity and PCB wiring -> back up original internal Flash -> run silent loopback -> connect USB-CAN for version/status -> identify fitted NOR before implementing/enabling real storage writes.
- Existing L1 core uses fixed APP 0x08010000, external old/new containers and transactional EEPROM metadata.
- Keil bootloader/app projects, ZG/ZE scatter layouts, four port adapters, CAN queues, deferred reset, application health and handoff hooks are present.
- Default BSP fails closed. The optional Explorer BSP configures documented common reference pins/clocks; physical validation remains pending. An explicit W25Q128JV-IQ/JQ command driver exists, but is not linked into read-only Explorer targets.
- Host adapter checks: 177 default / 243 gates-enabled per capacity, zero failures.
- 4 groups of 19 ARM C objects compiled; HAL reference syntax checked.
- Full MDK assembly/link unavailable here due Windows DLL relocation error.
- Packer: 14 Python tests and C interoperability acceptance/rejection checked.
- No hardware validation, package signature, physical power-cut data, or complete OTA.
- PC ISO-TP/UDS client, explicit optional python-can backend and C ECU process are implemented.
- 45 PC checks passed; actual C core verifies duplicate-block handling, lost-commit ACK, BSC wrap, rollback and simulated power cut.
- Demonstration output: evidence/pc-roundtrip.json. Synthetic payloads are NOT executable App firmware.
- Corrected P2* wire encoding to 01 F4 (5 seconds), with a C regression assertion.
- Current core regression: 3050 checks, zero failures, 420 storage cut scenarios per capacity.

Reconnect checkpoint (2026-10-31): local v0.11 source hashes match the successful full verification. The versioned local v0.11 archive was finalized successfully (184030 bytes, 134 files, SHA256 60efe3726237aeed6d54e1c4531e226f93b63d01aedbf79b3c910c1e77550d3a). No code tests were repeated because the implementation has not changed.

Official board-materials checkpoint (2026-10-31):
- The seller's purchase year is 2023. PCB revision and full MCU/NOR/EEPROM markings remain unknown.
- Verified ATK's official resource center, separate V2/V3 pages, official product threads and both linked A-disc directory listings.
- The V3 official product thread was posted on 2022-11-30; purchase year does not select a BSP revision.
- Located the V2.2 schematic and IO table filenames, and the V3 schematic archive, hardware manual and HAL examples filenames.
- Share listings were readable, but file download returned errno=-20. No schematic/manual/example body was obtained. No pins, jumpers or storage part identities were inferred from filenames/photos.
- Added docs/ATK_EXPLORER_F407.md, docs/BOARD_RECEIPT.md and evidence/official-resources.json. Implementation and default gates are unchanged.
- Next concrete hardware milestone: identified board -> LED/UART -> CAN loopback -> physical CAN -> read-only UDS version/status.
- A fresh armlink --vsn probe still fails with the Windows invalid-system-DLL-relocation error; full target linking remains pending.

Purchase-record checkpoint (2026-10-31):
- Seller's original Alientek flagship-store order record identifies the board as Explorer V3 (Tmall ID 609294673401, "upgrade / 2022" tagline), SKU "Explorer + 3.5in 320x480", price self-consistent (board ~¥498 + screen = ¥626, 83% off = ¥519.58).
- Main chip STM32F407ZGT6 (1 MiB) per official tutorial, textbook and HQchip listings; the seller's "ZET" label is a mislabel, Explorer has no ZET6 variant.
- This is documentary evidence for model/generation (V3 + ZG), not physical verification. Fitted NOR part, PCB revision, EEPROM page size and actual MCU marking still require on-board reads.
- Implementation and default gates unchanged. Next unchanged: on-board read of Device ID / Flash capacity / NOR RDID / EEPROM -> then real-part storage binding.

Next software-only milestone:
- Protocol edge hardening completed; new regressions first reproduced 39 failed assertions, then passed after fixes.
- Target cooperative response-pending implemented and mock-checked; BSP must return/service within 1000 ms.
- Read-only update-status DID 0x0200 is complete; PC status reads actual C metadata through CAN.
- W25Q128JV-IQ/JQ EF4018 command driver and optional HAL glue are complete; 720 checks, including 31 transfer-failure points.
- SPI-command model + actual UDS/boot core completed two downloads, backup/install and rollback.
- verify_project.py runs the complete requested matrix and binds evidence to source hashes.
- Watchdog policy, optional HAL IWDG hooks, health-gated App feeding and old-App DOWNLOADING health acceptance are complete.
- Real App entry: 35 checks / 8 virtual-watchdog scenarios per capacity; actual LSI/reset still unverified.
- Explicit staged-install recovery is complete; lost commit ACK -> status match -> verification/reset, without another download.
- Lost reset ACK is reported separately and never automatically retried; 45 PC tests and a reproducible recovery demo pass.
- Actual python-can 4.6.1 virtual backend: 5 additional tests passed against the actual C ECU process; no USB hardware was accessed.
- Hardware acceptance sequence and empty measurement CSV are present.
- Next: staged physical acceptance of the reference BSP and an actual-part storage binding. GNU read-only target assembly/link is complete; production upgrade Bootloader/App targets remain unconfigured. GPIO/UART power-cut stage markers remain unbound.
- Default write/handoff/watchdog gates remain disabled until actual BSP timing and hardware validation.
- Keep default target write/handoff gates disabled; do not install USB drivers or touch real CAN hardware during validation.
- Save each completed stage and its evidence before moving on.
