# AGENTS.md — Backtest Engine + AI Agent Loop Engineering (Stress Testing)

## Mission

เพิ่มความสามารถ backtest + stress-testing ให้บอท Avellaneda-Stoikov (`mm-bot-okx`)
โดย**ไม่แก้โค้ด production เดิม** สร้างเป็นระบบแยกในโฟลเดอร์ `backtest/` ที่:

1. รัน trading logic เดิม (`quote_engine.py`, `market_state.py`, `regime_detector.py`,
   `risk_manager.py`, `fill_tracker.py`) กับข้อมูลย้อนหลัง/synthetic แทนข้อมูล exchange จริง
2. สร้าง synthetic price/order-book scenario สำหรับ stress test (high-vol regime,
   flash crash, super bullish/bearish ตามที่ระบุ)
3. คำนวณ risk metrics (Sortino, MaxDD, VaR, ES/cVaR) จากผลรัน
4. วาง loop ให้ AI Agent ออกแบบ scenario → รัน backtest → ประเมินผล → เสนอแผนปรับปรุง
   แบบมี stopping condition ที่วัดได้จริง (ไม่ใช่ agent อ้างว่า "เสร็จแล้ว")

## Non-goals (สำคัญ — อย่าทำ)

- ห้ามแก้ `strategy.py`, `order_manager.py`, หรือโค้ดที่ต่อ exchange จริงใดๆ
- ห้ามให้ AI Agent แก้พารามิเตอร์ (`gamma`, `k`, `inventory_skew_factor` ฯลฯ) ใน
  `config.py` โดยอัตโนมัติ — ต้อง**เสนอเป็น diff ให้คนอนุมัติก่อนเสมอ** เพื่อป้องกัน
  blind parameter optimization ที่นำไปสู่ overfitting
- ห้าม fetch ข้อมูล exchange จริงระหว่าง backtest (network calls ทั้งหมดต้องถูก mock)

## โมดูลเดิมที่ต้อง reuse ตรงๆ (ห้ามเขียนใหม่)

| โมดูล | Class/ฟังก์ชัน | ใช้ยังไงใน backtest |
|---|---|---|
| `market_state.py` | `MarketState`, `EMA` | ป้อน synthetic order book dict (รูปแบบ CCXT: `{bids, asks, timestamp}`) เข้า `update_from_orderbook()` ทุก tick |
| `regime_detector.py` | `RegimeDetector.detect()` | ป้อน `ms.price_history_prices` ทุก tick เหมือนใน `strategy.py` |
| `quote_engine.py` | `QuoteEngine.generate()` | เรียกทุก tick ด้วย `ms`, `inventory`, `market_info`, multiplier จาก regime detector |
| `risk_manager.py` | `RiskManager.check_all()`, `update_pnl()` | เรียกทุก tick ก่อน quote generation เหมือน production loop |
| `fill_tracker.py` | `FillTracker._process_fill()` | **ไม่เรียก `detect_fills()`** (ต้องต่อ exchange) — ให้ matching engine สร้าง `Fill` object เองแล้วเรียก `_process_fill()` ตรงๆ |

## ต้องสร้างใหม่

### 1. `backtest/synthetic_data.py`

Generator 3 แบบ (แต่ละแบบ return `list[dict]` เป็น synthetic order book ticks
รูปแบบเดียวกับที่ `MarketState.update_from_orderbook()` รับ):

- `generate_regime_switching_gbm(vol_weekly, regime_duration_days, jump_freq, jump_size, seed)`
  — GBM ธรรมดา + Markov switch ระหว่าง (range, trend-up, trend-down) + jump component
- `generate_block_bootstrap(historical_returns, block_size, n_days, seed)`
  — สุ่มตัด block จาก historical return จริง (เก็บ BTC 2021 bull, 2022 bear, crash
    event ไว้เป็น library ของ block ให้เลือกผสม)
- `generate_garch_path(historical_prices, n_days, seed)` — fit GARCH(1,1) ด้วย
  library `arch` แล้ว simulate path ใหม่ที่มี volatility clustering

แต่ละ tick ต้องสร้าง synthetic `spread`/`imbalance` ที่กว้างขึ้นตามสภาวะ high-vol
(ไม่ใช่ constant spread) เพื่อให้ risk_manager/quote_engine เห็นสภาพตลาดสมจริง

### 2. `backtest/matching_engine.py`

หัวใจของ backtest — จำลองว่า limit order ของเราถูก fill เมื่อไหร่:

- **v1 (เริ่มตรงนี้):** ถ้าราคา synthetic tick ถัดไปแตะหรือทะลุ `bid_price`/`ask_price`
  ที่เรา quote ไว้ → fill เต็มไม้ที่ราคานั้น (optimistic, ไม่มี queue)
- **v2 (ทำทีหลัง):** เพิ่ม fill probability ตาม synthetic depth/imbalance ที่ level
  นั้น + slippage
- Output: สร้าง `Fill` object (จาก `fill_tracker.Fill`) แล้วส่งเข้า
  `FillTracker._process_fill()`

### 3. `backtest/runner.py`

Event loop ที่ mirror `strategy.py::on_trading_iteration()` ทุก step (1-9) แต่:
- แทน step 1 (`_update_market_data`) ด้วยการดึง tick ถัดไปจาก synthetic data
- แทน step 3 (`detect_fills`) ด้วย `matching_engine.check_fills()`
- ไม่มี step web dashboard/state persistence (ไม่จำเป็นใน backtest)
- เก็บ equity curve, drawdown, kill-switch events ทุก tick ไว้สำหรับคำนวณ metrics

### 4. `backtest/metrics.py`

ใช้ `empyrical` หรือ `quantstats` คำนวณจาก equity curve ที่ได้จาก runner:
- Sortino Ratio, Max Drawdown, VaR (95%/99%), ES/cVaR (95%/99%)
- เพิ่มเมทริกเฉพาะบอทนี้: kill-switch trigger count, fill rate (bid/ask), เวลาที่
  อยู่ในสถานะ inventory เกิน 80% ของ `max_inventory`

### 5. `backtest/agent_loop.py`

Loop Engineering ระดับบนสุด — 5 sub-loop:

1. **Scenario Designer** — เลือก/สุ่ม parameter scenario (vol level, regime duration,
   jump frequency) จาก config ที่กำหนดขอบเขตไว้ล่วงหน้า (ไม่ให้ agent สุ่มขอบเขตเอง)
2. **Synthetic Data Tool** — เรียก `synthetic_data.py` ตาม spec จากข้อ 1
3. **Backtest Runner** — เรียก `runner.py`
4. **Metrics Tool** — เรียก `metrics.py`
5. **Evaluator** — เทียบผลกับเกณฑ์ที่ตั้งไว้ล่วงหน้า (ดูหัวข้อ Acceptance Criteria)
   แล้ว**เสนอ**แผนปรับ (ไม่ auto-apply)

Stopping condition ต้องเป็นเงื่อนไขที่ตรวจสอบได้จริงจากตัวเลข เช่น:
> "ผ่าน scenario 20 แบบติดกัน โดย MaxDD < 15% และไม่มี position ที่ชน liquidation
> distance threshold"

ห้ามใช้เงื่อนไข "agent บอกว่าเสร็จแล้ว"

## Acceptance Criteria (ตัวอย่างเกณฑ์เริ่มต้น — ปรับตามความเสี่ยงที่รับได้จริง)

- [ ] รัน scenario "weekly vol 25-30%, regime switch bull/bear" ได้อย่างน้อย 20 รอบ
      (seed ต่างกัน) โดยไม่มีรอบไหน MaxDD เกิน `max_drawdown_pct` ใน config แบบ
      "หลุดเกณฑ์เงียบๆ" (kill-switch ต้อง trigger ตามที่ควรจะเป็น ไม่ใช่ position
      ค้างเกินขีดจำกัด)
- [ ] cVaR (95%) ของ equity curve ทุก scenario อยู่ในเกณฑ์ที่ยอมรับได้ (กำหนดตัวเลข
      จริงก่อนเริ่มรัน ไม่ใช่ดูผลแล้วค่อยตั้งเกณฑ์ย้อนหลัง)
- [ ] Matching engine v1 ผ่าน unit test เทียบกับ known fill scenario (ราคาผ่าน bid
      ต้อง fill, ราคาไม่ผ่านต้องไม่ fill)

## โครงสร้างไฟล์เป้าหมาย

```
mm-bot-okx/
├── strategy.py            (ไม่แตะ)
├── quote_engine.py         (ไม่แตะ — reuse)
├── market_state.py         (ไม่แตะ — reuse)
├── regime_detector.py      (ไม่แตะ — reuse)
├── risk_manager.py         (ไม่แตะ — reuse)
├── fill_tracker.py         (ไม่แตะ — reuse)
└── backtest/
    ├── __init__.py
    ├── synthetic_data.py
    ├── matching_engine.py
    ├── runner.py
    ├── metrics.py
    ├── agent_loop.py
    └── scenarios/           (scenario config YAML/JSON, ไม่ hardcode ในโค้ด)
```

## Dependencies เพิ่ม

```
arch          # GARCH modeling
empyrical     # risk metrics (Sortino, MaxDD, VaR)
quantstats    # ทางเลือกแทน empyrical (มี report HTML built-in)
```

## ลำดับการสร้าง (แนะนำ)

1. `matching_engine.py` (v1 optimistic fill) + unit test
2. `synthetic_data.py` (เริ่มจาก regime-switching GBM อย่างเดียว)
3. `runner.py` (ต่อทุกอย่างเข้าด้วยกัน รันจบ 1 scenario ได้)
4. `metrics.py`
5. `agent_loop.py` (ต่อ AI agent ทีหลังสุด หลัง pipeline พื้นฐานรันได้แล้ว)
