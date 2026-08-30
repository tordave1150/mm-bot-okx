# R1 Network-Capable Read-Only Demo Preflight — Implementation Plan

วันที่จัดทำ: 2026-08-29  
สถานะเอกสาร: P0 completed — ยังไม่อนุญาตให้ prepare หรือ run R1

## 1. Objective

ยืนยันว่า successor market-maker สามารถอ่านและ reconcile สถานะบัญชี/ตลาดบน
OKX Demo ผ่าน environment ที่ออก network ได้ โดยคงขอบเขต read-only อย่างเคร่งครัด:

- ไม่ create, amend, cancel หรือ flatten order
- ไม่เปลี่ยน leverage, position mode หรือ account configuration
- ไม่เรียก Live endpoint
- ไม่ใช้ production credentials หรือ production mode
- ไม่ reuse package/run/session/token identity
- ไม่อนุญาต R2 หรือ production โดยอัตโนมัติ

## 2. Current verified baseline

Fresh R0 evidence ปัจจุบัน:

```text
execution-environment-transport-repair-offline-20260829T105821Z
```

ผลล่าสุด:

| Scope | Result |
|---|---:|
| Execution-environment targeted | 51 passed |
| Successor targeted | 119 passed |
| Root non-Optuna | 453 passed |
| Backtest non-Optuna | 313 passed |
| Network/credential/endpoint/order/mutation attempts | 0 |

R0 นี้ยังไม่ให้สิทธิ์ R1 preparation หรือ R1 execution

## 3. P0 blocker ที่ต้องแก้ก่อน R1

`okx_fill_restart_preflight_prepare.py` จัด evidence kind
`execution_environment_transport_r0_offline_repair` เป็น multi-session R1
preparation protocol แต่ `run_read_only_preflight()` ใน
`okx_fill_restart_preflight.py` ยังไม่รวม evidence kind นี้ในชุดที่เลือก
`MULTI_SESSION_A1_PREFLIGHT_PROTOCOL_ID`

ผลกระทบที่เป็นไปได้:

- preparation package และ armed preflight marker อาจผูกกับ protocol ID คนละชุด
- test ปัจจุบันยังไม่พิสูจน์ binding นี้แบบ end-to-end
- การเดินหน้าด้วย current R0 evidence หลังแก้ source จะทำให้ source hashes stale

### Completed implementation

1. สร้าง `MULTI_SESSION_A1_R0_EVIDENCE_KINDS` เป็นชุดกติกากลาง และเพิ่ม
   `execution_environment_transport_r0_offline_repair` ลงในชุดนั้น
2. ให้ทั้ง R1 execution และ R1 preparation ใช้ชุดกติกาเดียวกัน เพื่อลดโอกาส
   protocol binding drift
3. เพิ่ม regression test ที่ทำให้ preflight จบแบบ fail-closed ก่อน read dispatch
   แล้วตรวจ `READ_ONLY_PREFLIGHT_ARMED.json` ว่าใช้
   `MULTI_SESSION_A1_PREFLIGHT_PROTOCOL_ID` สำหรับ evidence นี้
4. ทดสอบ targeted ผ่าน 51 รายการ และ full permitted R0 ผ่านด้วย blank credentials
   กับ socket denial: successor targeted 119, root non-Optuna 453 และ backtest
   non-Optuna 313
5. สร้าง fresh non-overwriting R0 evidence
   `execution-environment-transport-repair-offline-20260829T105821Z`; terminal marker
   เขียนเป็นไฟล์สุดท้าย และ audit ยืนยัน network/credential/order/mutation เป็น 0

ห้ามใช้ `execution-environment-transport-repair-offline-20260829T103745Z` สำหรับ R1
เพราะ source hash stale หลัง P0 patch ให้ใช้เฉพาะ fresh R0 evidence
`execution-environment-transport-repair-offline-20260829T105821Z` ตาม checkpoint A

## 4. Authorization checkpoint A — R1 preparation

R1 preparation เป็นงาน offline และ socket-denied แต่ยังต้องได้รับ exact user
authorization แยกต่างหากก่อนสร้าง identities

### Required authorization scope

```text
อนุญาตเฉพาะการเตรียม fresh R1 read-only OKX Demo preflight package
จาก fresh R0 evidence <REPAIR_ID> แบบ offline/socket-denied เท่านั้น
ไม่อนุญาตให้อ่าน credentials, ติดต่อ network, รัน preflight, ส่ง order,
เปลี่ยน account configuration, ใช้ Live endpoint, prepare R2 หรือ production
```

### Preparation command template

ห้ามรันจนกว่าจะได้รับ checkpoint A:

```powershell
.\myenv\Scripts\python.exe .\okx_fill_restart_preflight_prepare.py `
  --repair-id <FRESH_R0_REPAIR_ID>
```

ระบบต้องสร้าง identities ใหม่เอง:

```text
preparation_id = preflight-package-<fresh UTC timestamp>
run_id         = preflight-<fresh UTC timestamp>
session_id     = preflight:<run_id>:p0:<fresh nonce>
arm_token      = OKX_DEMO:<session_id>
```

### Preparation acceptance gates

- R0 terminal, decision, completion hashes และ source hashes verify ผ่าน
- preparation/run paths ยังไม่มีอยู่
- socket guard เปิดและ network attempts เท่ากับ 0
- credentials loaded เท่ากับ false
- orders/mutations/account configuration attempts เท่ากับ 0
- `preflight_authorized: false`
- `preflight_executed: false`
- arm token ไม่ถูก serialize ลง artifact; เก็บเฉพาะ SHA-256
- `PREFLIGHT_PREPARATION_COMPLETED.json` ถูกเขียนหลัง completion hashes

หลัง preparation เสร็จให้หยุด ห้ามรัน preflight ต่อใน turn เดียวกัน

## 5. Environment qualification before checkpoint B

R1 execution ต้องเกิดบน environment ที่ผู้ใช้อนุญาตและออก network ได้จริง
ไม่ใช่ offline Codex sandbox ที่เคยทำให้ predecessor preflight ล้ม

Environment requirements:

- DNS/TCP/TLS ไปยัง public OKX Demo hostname ใช้งานได้
- system clock/skew อยู่ใน frozen limits
- Python environment และ source tree ตรงกับ preparation source hashes
- ใช้ OKX Demo/simulated trading transport เท่านั้น
- credentials ครบสำหรับ Demo account และไม่ถูก serialize
- permission ต้องมี read-only และ trade แต่ไม่มี withdraw
- ห้าม probe account/credentials ก่อน checkpoint B
- ห้ามแก้ proxy/CA/network policy แบบกว้างโดยไม่มีการอนุมัติแยก

การทดสอบ network capability ต้องไม่ reuse predecessor run ID และต้องไม่ถือว่า
pip/PyPI connectivity เป็นหลักฐานว่า OKX Demo transport ใช้งานได้

## 6. Authorization checkpoint B — execute R1 read-only preflight

ต้องได้รับ exact confirmation ใหม่ซึ่งระบุ identifiers ทุกตัวจาก preparation
โดยตรง ห้ามใช้ placeholder และห้ามเดา token

### Exact authorization template

```text
อนุญาตให้รัน R1 READ_ONLY_OKX_DEMO_PREFLIGHT บน network-capable environment
สำหรับ repair_id=<EXACT_REPAIR_ID>
preparation_id=<EXACT_PREPARATION_ID>
run_id=<EXACT_RUN_ID>
session_id=<EXACT_SESSION_ID>
arm_token=<EXACT_ARM_TOKEN>

อนุญาตเฉพาะ read-only OKX Demo requests ที่จำเป็นต่อ preflight
ไม่อนุญาต create/amend/cancel/flatten order, mutation retry,
account configuration change, Live endpoint, R2 หรือ production
```

การอนุญาต checkpoint A ห้ามนำมาใช้แทน checkpoint B

### Execution command template

ห้ามรันจนกว่าจะได้รับ checkpoint B:

```powershell
.\myenv\Scripts\python.exe .\okx_fill_restart_preflight.py `
  --run-id <EXACT_RUN_ID> `
  --offline-run-id <EXACT_REPAIR_ID> `
  --preparation-id <EXACT_PREPARATION_ID> `
  --session-id <EXACT_SESSION_ID> `
  --arm-token <EXACT_ARM_TOKEN>
```

ห้ามบันทึก command พร้อม plaintext arm token ลง artifact, log, Markdown หรือ shell
history ที่แชร์ภายนอก Token ต้องส่งให้ process เพียงครั้งเดียวและ artifact เก็บเฉพาะ hash

## 7. R1 read-only gates

Preflight ต้องอ่าน snapshot สองครั้งและผ่านทุก gate:

### Identity and evidence

- exact run/preparation/repair/session/token binding
- fresh run path; collision ต้อง fail
- source hashes ต้องตรง preparation
- immutable predecessor และ fresh R0 evidence verify ผ่าน

### Transport

- `sandboxMode: true`
- simulated trading header ถูกส่ง
- expected Demo hostname/endpoints ถูกพิสูจน์ทุก read dispatch
- Live endpoint attempts เท่ากับ 0
- mutation attempts เท่ากับ 0

### Account and permissions

- account binding เหมือนกันทั้งสอง snapshot
- position mode เป็น `net_mode`
- leverage เป็น `3x`
- position เป็น 0 BTC ทั้งสอง snapshot
- open orders เป็น 0 ทั้งสอง snapshot
- owned open orders เป็น 0
- kill switch/state/flatten state resolved
- permissions มี read-only และ trade แต่ไม่มี withdraw
- permission snapshots ทั้งสองครั้งเหมือนกัน

### Market, fees, and capacity

- instrument เป็น `BTC/USDT:USDT`
- book ไม่ stale/crossed/future/empty
- timestamp เดินหน้าและผ่าน clock-skew gate
- maker/taker fee ตรง frozen profile
- fixed lot เป็น 0.01 BTC
- two-sided margin อยู่ภายใน frozen 750 USDT / 3x / 80% capacity gate

## 8. Failure handling

ทุก failure ต้อง fail closed และเขียน evidence เท่าที่ปลอดภัย:

| Failure | Required behavior |
|---|---|
| Network/DNS/TLS ถูกบล็อก | หยุดโดยไม่มี mutation; ห้ามสรุปว่า credentials/OKX ผิดหาก HTTP ไม่ถูกส่ง |
| Credentials ไม่ครบ | หยุดก่อน account call; ไม่ serialize secret |
| Demo transport พิสูจน์ไม่ได้ | refuse read dispatch |
| Source/hash drift | หยุดก่อน network |
| Identity collision | ห้าม overwrite/resume |
| Position หรือ open orders ไม่เป็นศูนย์ | R1 failed; ห้าม cancel/flatten |
| Permission มี withdraw หรือ permission snapshots ไม่ตรง | R1 failed |
| Book/clock/fee/capacity gate fail | R1 failed |
| Terminal reconciliation ไม่ authoritative | fail closed; ห้ามสร้าง success marker เท็จ |

Mutation retry ต้องเป็น 0 เสมอ Failure ไม่อนุญาต reuse identity หรือ recover in place
การลองใหม่ต้องเริ่มด้วย offline diagnosis, fresh R0/R1 identities และ authorization ใหม่

## 9. Evidence outputs

Expected R1 run output:

```text
artifacts/okx_demo_fill_restart_validation/<EXACT_RUN_ID>/
  READ_ONLY_PREFLIGHT_ARMED.json
  predecessor/
  specification/
  preflight/preflight_result.json
  audits/endpoint_audit.json
  audits/secret_scan.json
  decision/preflight_decision.json
  completion_hashes.json
  PREFLIGHT_PHASE_COMPLETED.json
```

Success criteria:

- `READ_ONLY_PREFLIGHT_PASSED`
- `read_only_preflight_passed: true`
- orders submitted/amended/cancelled/flattened เท่ากับ 0
- account setter calls เท่ากับ 0
- Live endpoint attempts/orders เท่ากับ 0
- secret scan passed
- completion hashes verify
- terminal marker written last

## 10. Post-R1 boundary

ถ้า R1 ผ่าน:

1. ตรวจ hashes, terminal state, position/open orders `0 / 0`
2. รายงาน metrics และ exact R1 identities โดยไม่เปิดเผย credentials/token
3. หยุดและขอ authorization แยกสำหรับ R2 preparation
4. R2 execution ต้องมี authorization ใหม่อีกครั้ง

ถ้า R1 ไม่ผ่าน:

1. หยุดทันที
2. ห้าม prepare/run R2
3. ห้าม reuse identities หรือ mutation recovery
4. สร้าง offline failure audit ใหม่แบบ non-overwriting

R1 ไม่ว่าผ่านหรือไม่ผ่านไม่เคยอนุญาต production

## 11. Definition of done

- P0 protocol binding defect ถูกแก้และมี regression tests
- fresh R0 evidence หลัง patch ผ่านทุก permitted gate
- checkpoint A ได้รับ exact authorization ก่อน preparation
- fresh R1 package ถูกสร้างโดย network attempts 0
- checkpoint B ได้รับ exact identity-scoped authorization
- R1 รันบน network-capable Demo-only environment
- mutation/order/account changes/Live attempts เป็น 0
- evidence และ terminal hashes verify
- ไม่มี identity reuse
- ไม่มี R2/production action

## 12. Current safety status

```text
production_authorized: false
live_mode_available: false
live_endpoint_attempts: 0
live_orders: 0
optuna_executed: false
validation_opened: false
holdout_opened: false
git_write_operation: false
R1_preparation_authorized: false
preflight_authorized: false
```
