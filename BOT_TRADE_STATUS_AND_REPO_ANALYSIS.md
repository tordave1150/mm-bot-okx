# วิเคราะห์สถานะ `bot-trade` และแนวทางจัดระเบียบ Repo

วันที่วิเคราะห์: 2026-08-28  
ขอบเขต: local/offline read-only analysis ไม่มีการติดต่อ OKX, เปิด credentials, รัน Optuna, validation, holdout หรือแก้ไข Git

## Executive summary

`bot-trade` อยู่ในระดับ **R0 — Advanced Offline/Demo Safety Research** ระบบกลยุทธ์ การจำลอง และ safety evidence มีความก้าวหน้าค่อนข้างสูง แต่ยังไม่ใช่บอตที่พร้อมเปิดใช้งานจริงหรือ production

| ด้าน | ระดับโดยประมาณ | เหตุผล |
|---|---:|---|
| กลยุทธ์และ simulation | 8/10 | มี market-maker model, accounting, FIFO, fill และ risk fixtures จำนวนมาก |
| Offline safety/evidence | 8/10 | มี fail-closed protocol, socket denial และ immutable evidence |
| Test coverage | 8/10 | Stored R0 evidence แสดง targeted/root/backtest gates ผ่าน |
| ความเป็นระเบียบของ repo | 4/10 | มี runtime สองสาย, scripts/evidence หลาย generation และ artifacts จำนวนมาก |
| ความพร้อมใช้งาน OKX Demo ตอนนี้ | 3/10 | R1/preflight ยังไม่ได้รับอนุญาต และ virtual environment ถูกลบแล้ว |
| Production readiness | 0/10 | Production และ production read-only shadow ไม่ได้รับอนุญาต |

## Stored evidence ที่พบ

## Fresh execution record — 2026-08-28

The R0 workflow in this report was executed after the analysis documents and dependency sets were updated.

| Item | Result |
|---|---|
| Environment | Recreated in `myenv/` with `requirements-offline-test.txt` |
| Optuna | Not installed |
| Fresh evidence ID | `execution-environment-transport-repair-offline-20260828T164525Z` |
| Preflight-focused targeted suite | 46 passed |
| Successor targeted suite | 119 passed |
| Root non-Optuna suite | 451 passed |
| Backtest non-Optuna suite | 313 passed |
| Socket/network attempts | 0; socket-denied guard enabled |
| Credential reads/serialization | 0 / false |
| Live orders/mutations | 0 |
| Completion marker | Written last; completion-hashes SHA-256 `51dc8620c4f85bd990254eb44acff05e8a190218245961794cf1da978e9a8f4f` |

This fresh R0 result does not authorize R1, preflight, an economic campaign, or production.

## Diagnosis and fix — 2026-08-29

The canonical successor stack was not failing its offline gates. The observed
preflight failure belonged to an immutable predecessor run executed inside a
network-restricted sandbox; no HTTP request, credential access, account call,
or mutation occurred.

A separate local safety issue was found in the retained legacy stack:

- `bot_state.json` is stale and records inventory `-0.01 BTC`.
- the former `main.py` path could load configuration/dotenv before an operator
  learned that this runtime is not authorized under the active R0 protocol.

The fix keeps the state unchanged as recovery evidence and makes `main.py` fail
closed before `load_config()`, dotenv, credentials, transport, or state restore.
`tests/test_main_legacy_runtime_guard.py` proves the guard cannot load config.

Fresh evidence ID:

```text
execution-environment-transport-repair-offline-20260829T103745Z
```

| Test scope | Result |
|---|---:|
| Execution-environment targeted, including legacy guard | 48 passed |
| Successor targeted | 119 passed |
| Root non-Optuna | 453 passed |
| Backtest non-Optuna | 313 passed |

The fresh source manifest hashes `README.md`, `main.py`, and the new regression
test. Network, credential reads, Demo/Live endpoints, create, amend, cancel,
flatten, account mutation, orders, and mutation retries all remained zero.

R0 evidence ล่าสุดที่ตรวจพบระบุว่า:

| Test scope | Passed | Network attempts | Optuna imported/executed |
|---|---:|---:|---:|
| Root non-Optuna | 451 | 0 | false |
| Targeted successor tests | 119 | 0 | false |
| Backtest non-Optuna | 313 | 0 | false |

ข้อจำกัดของผลนี้:

- เป็น stored evidence ไม่ใช่การทดสอบใหม่ในรอบวิเคราะห์นี้
- environment เดิมถูกลบระหว่าง repo cleanup แต่สร้าง fresh `myenv/` ใหม่แล้วด้วย dependency set ที่ไม่รวม Optuna
- R0 passed ไม่ได้หมายความว่า R1, preflight, economic campaign หรือ production ได้รับอนุญาต

สถานะ promotion ที่บันทึกไว้:

```text
R0_offline_repair_passed: true
R1_preparation_authorized: false
preflight_authorized: false
economic_campaign_authorized: false
production_authorized: false
```

## Economic deficits ที่ยังต้องแก้

Immutable predecessor campaign จบด้วย `INSUFFICIENT_EVIDENCE` และมี deficit ดังนี้:

| Metric | ผลเดิม | Gate | Deficit |
|---|---:|---:|---:|
| Normal maker fills | 15 | อย่างน้อย 24 | ขาด 9 |
| Bid fills | 7 | อย่างน้อย 8 | ขาด 1 |
| FIFO maker round trips | 5 | อย่างน้อย 8 | ขาด 3 |
| Sessions ที่ใช้ special flatten | 5/12 | ไม่เกิน 2/12 | เกิน 3 |

ข้อมูลสำคัญเพิ่มเติม:

- normal net PnL: `+0.3052058 USDT`
- aggregate net PnL รวม special economics: `-2.044501705 USDT`
- มี 4 sessions ที่ไม่มี fill
- มี 5 one-fill sessions ที่จบด้วย terminal flatten

ดังนั้นจุดที่ควรแก้ต่อคือ maker sample efficiency, FIFO work-off และการลดการพึ่งพา terminal flatten โดยต้องไม่เพิ่ม risk budget เดิม

## ปัญหาโครงสร้าง: มีสอง runtime stack

### 1. Original async bot stack

Entry point:

```text
main.py
  -> trading_bot.py
  -> config.py
  -> market_state.py
  -> quote_engine.py
  -> order_manager.py
  -> fill_tracker.py
  -> risk_manager.py
  -> dashboard/web server
```

ลักษณะ:

- ใช้ `main.py` เป็น entry point
- อ่าน credentials และสร้าง CCXT exchange
- ใช้ profile เดิมประมาณ 300 USDT และ 1x
- README ปัจจุบันยังอธิบาย stack นี้เป็นการใช้งานหลัก

### 2. Controlled OKX Demo successor stack

องค์ประกอบหลัก:

```text
market_maker/
okx_demo_*.py
okx_fill_restart_*.py
okx_execution_safety.py
tests/test_okx_*.py
backtest/mm_*.py
```

ลักษณะ:

- ใช้ frozen market-maker profile
- มี identity, phase และ evidence boundaries
- มี socket denial, mutation audit, state recovery และ terminal reconciliation
- protocol ปัจจุบันกำหนด modeled capital 750 USDT และ leverage 3x
- production และ Live mode ถูกปิด

### ข้อสรุป

ควรกำหนด canonical stack ให้ชัดเจนเพียงชุดเดียว แนะนำให้ successor stack เป็นสายหลัก และทำเครื่องหมาย original async stack ว่าเป็น legacy/experimental จนกว่าจะมีการตัดสินใจว่าจะรวมเข้ากับ successor หรือเลิกใช้

## เอกสารที่ต้องแก้ก่อนทำงานต่อ

### `README.md`

README ปัจจุบันมีข้อมูลที่ไม่ตรงกับ source/protocol:

- ระบุว่า `strategy.py` เป็น controller หลัก แต่ `main.py` import `TradingBot` จาก `trading_bot.py`
- แนะนำให้ใส่ API credentials และรัน `python main.py` ซึ่งไม่อยู่ในขอบเขตที่ protocol ปัจจุบันอนุญาต
- ระบุทุน 300 USDT และ 1x ขณะที่ successor protocol ใช้ 750 USDT และ 3x
- มีคำสั่ง Optuna/validation/holdout ที่ห้ามใช้ใน phase ปัจจุบัน

ควรแก้ README ให้เริ่มจากข้อความว่า repo อยู่ใน offline-only R0 และแยกหัวข้อ legacy runtime ออกจาก successor workflow

### `requirements.txt`

ไฟล์เดียวรวม dependencies ทุกประเภท รวมถึง Optuna ควรแยกเป็น:

```text
requirements-runtime.txt
requirements-offline-test.txt
requirements-research-disabled.txt
```

กลุ่ม Optuna, validation และ holdout ต้องอยู่ในไฟล์ที่ระบุชัดว่า disabled by current protocol

## การจัดประเภทไฟล์และโฟลเดอร์

### A. Core — ควรเก็บไว้

| Path/group | บทบาท |
|---|---|
| `AGENTS.md` | active protocol และ authorization boundary |
| `AGENTS_OKX_DEMO_ECONOMIC_SAMPLE_EFFICIENCY_REPAIR.md` | successor source ที่ source/tests อ้างโดยตรง |
| `market_maker/` ยกเว้น cache | active strategy/model สำหรับ successor และ backtest |
| `okx_demo_*.py` | demo campaign, controller, supervisor, repair และ evidence logic |
| `okx_fill_restart_*.py` | shared safety, validation model, gateway และ recovery foundation |
| `market_spec.py` | contract/quantity conversion และ order validation |
| `fill_tracker.py`, `fill_classification.py` | fill accounting และ classification |
| `tests/` | root safety/regression tests |
| `backtest/` | offline simulation และ non-Optuna tests |
| `static/index.html` | ถูกอ่านโดย `web_server.py` |
| `artifacts/okx_demo_multi_session_economic_soak/packages/economic-package-20260818T125221Z/` | immutable predecessor package |

### B. Legacy candidates — ยังเกี่ยวข้อง แต่ควรย้ายออกจาก active path

| Path | Evidence | คำแนะนำ |
|---|---|---|
| `strategy.py` | ไม่มี Python module อื่น import และ `main.py` ใช้ `TradingBot` | ย้ายไป `legacy/lumibot/` หลังแก้ README/tests/comments |
| `.lumibot/` | database เก่าของ `AvellanedaMarketMaker` | archive หรือลบหลังยืนยันว่าเลิกใช้ `strategy.py` |
| `main.py`, `trading_bot.py` | เป็น original runtime stack ที่ไม่ใช่ successor campaign | ทำเครื่องหมาย legacy ก่อน ยังไม่ควรลบ |
| `bot_state.json` | state เก่าของ original runtime | archive หลังยืนยันว่าไม่ต้อง recovery |
| `AGENTS_MM_V1_3C_FILL_TRIGGER_ACTIVITY_EVIDENCE_REPAIR.md` | ไม่มี source อ้างชื่อไฟล์โดยตรง แต่เป็น protocol history | ย้ายเข้า protocol archive ไม่ควรลบทันที |

หมายเหตุ: โมดูลบางไฟล์ของ original stack เช่น `market_spec.py`, `fill_tracker.py` และ `utils.py` ถูก successor stack ใช้ร่วมด้วย จึงห้ามย้ายทั้งกลุ่มโดยดูจากชื่อเพียงอย่างเดียว

### C. Generated files — ลบได้

| Path/group | สถานะ |
|---|---|
| `__pycache__/`, `market_maker/__pycache__/`, `backtest/__pycache__/` | Python bytecode สร้างใหม่ได้ |
| `.pytest_cache/` | pytest cache สร้างใหม่ได้ |
| `bot_state.json.corrupt` | ไฟล์ว่าง 0 bytes |
| `bot.log`, `bot_error.log` | log ว่างและสร้างใหม่ได้ |
| `.tmp_*`, `*_tmp` | generated test directories |

temp บาง directory ถูก Windows ACL ล็อกโดย sandbox account ต้องใช้ owner เดิมหรือ Administrator ในการลบ ห้ามแก้ ACL แบบกว้างทั้ง repo

### D. Related but currently prohibited — ห้ามใช้ ไม่ควรลบ

| Path | เหตุผล |
|---|---|
| `backtest/optimize.py` | Optuna research tool |
| `backtest/robust_optimize.py` | Optuna + validation/holdout workflow |
| `compare_params.py` | post-optimization comparison tool |
| `backtest/tests/test_robust_gates.py` | เกี่ยวข้องกับ disabled robust workflow |
| `backtest/tests/test_units_and_safety.py` | ถูก exclude จาก permitted R0 backtest scope |

ไฟล์เหล่านี้ยังเกี่ยวข้องกับงานวิจัย แต่ไม่อยู่ใน phase ที่อนุญาตให้ execute/import จึงควรแยกสถานะให้ชัดเจนแทนการลบ

## Historical artifacts ที่ไม่ใช่ runtime

กลุ่มเหล่านี้ไม่จำเป็นต่อการทำงานราย tick ของบอต แต่เป็น historical research/evidence:

| Artifact group | ขนาดโดยประมาณ |
|---|---:|
| `artifacts/mm_v1_1_post_admission_smoke/` | 410 MB |
| `artifacts/okx_demo_soak_validation/` | 307 MB |
| `artifacts/mm_v1_6_economic_viability/` | 234 MB |
| `artifacts/mm_v1_3_balanced_causal_smoke/` | 86 MB |
| `artifacts/mm_v1_5_timeboxed_drawdown_repair/` | 72 MB |
| `artifacts/mm_v1_4_targeted_causal_defense/` | 67 MB |
| `artifacts/mm_v1_3c_fill_trigger_activity_repair/` | 58 MB |
| `artifacts/mm_v1_3b_stress_resilience_repair/` | 53 MB |

แนวทางที่แนะนำ:

1. สร้าง manifest ของ package/run IDs, completion markers และ hashes
2. แยก immutable/current evidence ออกจาก historical research logs
3. ย้าย historical artifacts ไป external archive
4. ตรวจ archive ว่าเปิดและ hash ได้ก่อนลบจาก repo
5. ห้ามเปิด validation/holdout contents ระหว่าง phase ปัจจุบัน
6. ห้ามแตะ immutable predecessor package

## Roadmap ที่แนะนำ

### Priority 1 — Repo clarity

- เลือก canonical runtime stack
- แก้ README ให้ตรง active protocol
- เพิ่ม `ARCHITECTURE.md` อธิบาย original vs successor stack
- เพิ่ม manifest แยก core, legacy, generated และ evidence

### Priority 2 — Reproducible offline environment

- แยก requirements ตามหน้าที่
- สร้าง virtual environment ใหม่ด้วย permitted dependencies เท่านั้น
- ใช้ blank credentials และ socket denial
- ห้าม import/run Optuna, validation หรือ holdout

### Priority 3 — Fresh R0 verification

- รัน targeted successor tests
- รัน successor-applicable root non-Optuna tests
- รัน permitted backtest non-Optuna tests
- สร้าง fresh non-overwriting evidence และ terminal marker เขียนเป็นไฟล์สุดท้าย

### Priority 4 — Economic repair

- เพิ่ม balanced quote opportunity โดยไม่เพิ่ม create/inventory budgets
- ปรับ one-sided defense ให้รักษา causal maker work-off
- ลด terminal flatten dependency
- ปิด fill, FIFO และ special-flatten deficits โดยไม่ลด gate

### Priority 5 — Promotion boundary

หลัง fresh R0 gates ผ่านทั้งหมดแล้วเท่านั้น จึงขอ exact user authorization สำหรับเตรียม fresh R1 identities การเตรียมหรือรัน R1 และ R2 เป็นคนละ authorization และไม่อนุญาต production

## ห้ามทำโดยยังไม่มี authorization ใหม่

- ห้ามอ่าน credentials หรือสร้าง credential-bearing config
- ห้ามติดต่อ OKX endpoint
- ห้าม prepare/run R1 preflight
- ห้าม prepare/run R2 economic campaign
- ห้ามสร้าง แก้ไข ยกเลิก หรือ flatten order
- ห้ามเปลี่ยน leverage/account configuration
- ห้ามรัน Optuna, validation หรือ holdout
- ห้ามทำ Git operations

## Safety status

```text
production_authorized: false
live_mode_available: false
live_endpoint_attempts: 0
live_orders: 0
optuna_executed: false
validation_opened: false
holdout_opened: false
git_write_operation: false
```
