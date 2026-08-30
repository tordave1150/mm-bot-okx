# คู่มือเคลียร์ไฟล์ใน `bot-trade`

อัปเดตจากการตรวจแบบออฟไลน์เมื่อ 2026-08-28 คู่มือนี้เป็นเพียงแนวทางและคำสั่งสำหรับตรวจสอบ/ล้างไฟล์ **ไม่ได้อนุญาตให้รันบอต ติดต่อ OKX หรือแก้ไข Git**

## สรุปพื้นที่ปัจจุบัน

| โฟลเดอร์ | ขนาดโดยประมาณ | หมายเหตุ |
|---|---:|---|
| `artifacts/` | 1,435 MB | ใหญ่ที่สุด ส่วนมากเป็น `.jsonl` จาก run เก่า |
| `myenv/` | 1,419 MB | Python virtual environment สร้างใหม่ได้ |
| `.git/` | 86 MB | ห้ามลบหรือแก้ไข |
| temp/cache ที่ root | 10 MB | ล้างได้หลังยืนยันว่าไม่มี test กำลังทำงาน |
| `backtest/` | 2.6 MB | source/test; อย่าลบทั้งโฟลเดอร์ |
| `artifacts/optuna/` | 1.55 MB | ผล tune เก่า ไม่ใช่ต้นเหตุหลักที่ repo ใหญ่ |
| `artifacts/robust_optuna/` | 0.39 MB | ผล tune เก่า ไม่ใช่ต้นเหตุหลักที่ repo ใหญ่ |

ใน `artifacts/` ไฟล์ `.jsonl` รวมกันประมาณ 1,406 MB จึงควรจัดการ historical run logs มากกว่าลบ settings ของบอต

## สิ่งที่ห้ามลบหรือแก้ไข

- `.env` — credentials/secrets; ห้ามเปิดเผยหรือใส่ใน archive ที่จะแชร์
- `.git/` และ `.gitignore`
- `AGENTS.md` และไฟล์ `AGENTS_*.md`
- source/config เช่น `config.py`, `market_spec.py`, `strategy.py`, `risk_manager.py`, `requirements.txt`
- `bot_state.json` เว้นแต่บอตหยุดสนิทและมีแผน reset state ที่ตรวจสอบแล้ว
- completion marker, hash, registry และ decision ของ evidence package ที่ยังใช้อ้างอิง
- predecessor package นี้ทั้งโฟลเดอร์ ห้าม edit, rerun, extend, resume หรือ delete:

  ```text
  artifacts/okx_demo_multi_session_economic_soak/packages/economic-package-20260818T125221Z/
  ```

  identity ที่ต้องคงไว้:

  ```text
  economic-package-20260818T125221Z
  economic-campaign-20260818T125221Z
  economic-campaign-run-20260818T125221Z
  ```

ห้ามใช้คำสั่งกว้าง ๆ เช่น `Remove-Item artifacts -Recurse`, `Remove-Item . -Recurse` หรือ wildcard ที่อาจครอบ immutable evidence

## ขั้นที่ 1 — ตรวจขนาดก่อนทุกครั้ง

รันจาก root ของ repo ด้วย PowerShell:

```powershell
$rows = foreach ($dir in Get-ChildItem -LiteralPath . -Force -Directory) {
    $files = @(Get-ChildItem -LiteralPath $dir.FullName -Force -File -Recurse -ErrorAction SilentlyContinue)
    $bytes = ($files | Measure-Object -Property Length -Sum).Sum
    if ($null -eq $bytes) { $bytes = 0 }
    [pscustomobject]@{
        Name  = $dir.Name
        MiB   = [math]::Round($bytes / 1MB, 2)
        Files = $files.Count
    }
}
$rows | Sort-Object MiB -Descending | Format-Table -AutoSize
```

ดูไฟล์ใหญ่ที่สุดใน artifacts โดยไม่อ่านเนื้อหาไฟล์:

```powershell
Get-ChildItem -LiteralPath .\artifacts -Force -File -Recurse -ErrorAction SilentlyContinue |
    Sort-Object Length -Descending |
    Select-Object -First 50 @{Name='MiB';Expression={[math]::Round($_.Length / 1MB, 2)}}, FullName |
    Format-Table -AutoSize
```

## ขั้นที่ 2 — เคลียร์ cache และ temp ที่สร้างใหม่ได้

ต้องหยุด test และ Python process ของ repo ก่อน คำสั่งชุดนี้ใช้ path แบบ explicit เท่านั้น:

```powershell
Remove-Item -LiteralPath .\__pycache__ -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item -LiteralPath .\.pytest_cache -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item -LiteralPath .\backtest\__pycache__ -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item -LiteralPath .\.tmp_post_campaign_targeted -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item -LiteralPath .\.tmp_r0_expanded_targeted -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item -LiteralPath .\.tmp_r0_expanded_targeted_2 -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item -LiteralPath .\.tmp_r0_repair_targeted -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item -LiteralPath .\.tmp_r0_repair_targeted_2 -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item -LiteralPath .\formal_prepare_test_tmp -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item -LiteralPath .\tmp_tests_20260802T172100Z -Recurse -Force -ErrorAction SilentlyContinue
```

โฟลเดอร์ `.okx_fill_cursor_repair_tmp` และ `.okx_fill_restart_tmp` ว่างอยู่ ณ วันที่ตรวจ แต่ให้ลบเฉพาะเมื่อยืนยันว่าไม่มี process ใช้งาน

ล้าง log ว่าง/ไม่สำคัญโดยคงตัวไฟล์ไว้:

```powershell
Clear-Content -LiteralPath .\bot.log -ErrorAction SilentlyContinue
Clear-Content -LiteralPath .\bot_error.log -ErrorAction SilentlyContinue
```

## ขั้นที่ 3 — ลบ virtual environment เพื่อคืนพื้นที่ประมาณ 1.42 GB

ทำเมื่อพร้อมติดตั้ง dependencies ใหม่เท่านั้น และต้องหยุดทุก Python process ที่ใช้ environment นี้ก่อน:

```powershell
Remove-Item -LiteralPath .\myenv -Recurse -Force
```

สร้างใหม่ภายหลัง:

```powershell
py -m venv myenv
.\myenv\Scripts\python.exe -m pip install -r .\requirements.txt
```

การสร้าง environment ใหม่ต้องไม่ import/run Optuna, validation หรือ holdout ในช่วงที่ protocol ปัจจุบันห้ามไว้

## ขั้นที่ 4 — จัดการผล tune

ผล tune ใช้พื้นที่น้อย จึงควรลบเมื่อไม่ต้องใช้ประวัติ parameter comparison แล้วเท่านั้น:

```powershell
# ตรวจรายการก่อน (dry run)
Get-ChildItem -LiteralPath .\artifacts\optuna -Force -Recurse
Get-ChildItem -LiteralPath .\artifacts\robust_optuna -Force -Recurse

# ลบเฉพาะเมื่อ review แล้ว
Remove-Item -LiteralPath .\artifacts\optuna -Recurse -Force
Remove-Item -LiteralPath .\artifacts\robust_optuna -Recurse -Force
```

อย่าลบไฟล์ต่อไปนี้เพียงเพราะชื่อเกี่ยวกับ parameter/settings:

- `compare_params.py` — source สำหรับเปรียบเทียบ parameters
- `config.py` — source configuration หลัก
- `market_spec.py` — market/risk specification
- `.env` — secret configuration
- `bot_state.json` — runtime recovery state

## ขั้นที่ 5 — Historical artifacts ที่คืนพื้นที่ได้มาก

กลุ่มใหญ่ที่พบและควรเริ่ม review ก่อน:

| กลุ่ม | ขนาดโดยประมาณ |
|---|---:|
| `artifacts/mm_v1_1_post_admission_smoke/` | 410 MB |
| `artifacts/okx_demo_soak_validation/` | 307 MB |
| `artifacts/mm_v1_6_economic_viability/` | 234 MB |
| `artifacts/mm_v1_3_balanced_causal_smoke/` | 86 MB |
| `artifacts/mm_v1_5_timeboxed_drawdown_repair/` | 72 MB |
| `artifacts/mm_v1_4_targeted_causal_defense/` | 67 MB |
| `artifacts/mm_v1_3c_fill_trigger_activity_repair/` | 58 MB |
| `artifacts/mm_v1_3b_stress_resilience_repair/` | 53 MB |
| `artifacts/diagnostics/` | 46 MB |
| `artifacts/market_maker_v1_smoke/` | 40 MB |

กลุ่มเหล่านี้มี run logs/evidence ปะปนกัน จึงไม่ควรลบทั้งกลุ่มทันที ให้ใช้ขั้นตอนต่อไปนี้กับ **หนึ่ง run directory ต่อครั้ง**:

1. ยืนยันว่าไม่ใช่ predecessor package และไม่ใช่ evidence ที่ protocol ปัจจุบันอ้างอิง
2. ตรวจว่ามี summary, decision, completion marker และ hashes ที่ต้องเก็บหรือไม่
3. ถ้าต้องเก็บประวัติ ให้ archive ไป storage นอก repo ก่อน และตรวจว่า archive เปิดได้
4. ระบุ path เต็มแบบ explicit; ห้ามใช้ wildcard
5. ลบเฉพาะ run directory ที่ review แล้ว
6. วัดขนาด repo ซ้ำและจดรายการที่ลบ

ตัวอย่าง **dry run เท่านั้น**:

```powershell
$candidate = Resolve-Path -LiteralPath '.\artifacts\mm_v1_1_post_admission_smoke\RUN_DIRECTORY_TO_REVIEW'
$candidate.Path
Get-ChildItem -LiteralPath $candidate.Path -Force
Get-ChildItem -LiteralPath $candidate.Path -Force -File -Recurse |
    Measure-Object -Property Length -Sum
```

หลังตรวจ path แล้วจึงใช้ `Remove-Item -LiteralPath <EXACT_REVIEWED_PATH> -Recurse -Force` ทีละ path ห้ามสร้าง loop ลบหลาย run ในครั้งเดียว

## Checklist ก่อนลบ

- [ ] บอต, supervisor, test และ Python process ของ repo หยุดทั้งหมด
- [ ] ไม่มี order/session หรือ pending intent ที่ต้องใช้ state สำหรับ recovery
- [ ] path ที่จะลบอยู่ภายใน repo นี้และ resolve ได้ตรงตามที่คาด
- [ ] path ไม่ใช่ `.git`, `.env`, source/config, `bot_state.json` หรือ `AGENTS*.md`
- [ ] path ไม่อยู่ใต้ immutable predecessor package
- [ ] artifacts ที่ต้อง audit ถูก archive และตรวจสอบ archive แล้ว
- [ ] เริ่มจาก cache/temp หรือ `myenv` ก่อน historical evidence
- [ ] ใช้ `-LiteralPath` และ explicit path; ไม่มี wildcard
- [ ] จด path และขนาดที่ลบไว้ภายนอก evidence package

## ขอบเขตความปลอดภัยที่ยังคงเดิม

การ cleanup นี้ต้องเป็นงาน local/offline เท่านั้น และไม่เปลี่ยน risk/config ของบอต:

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

