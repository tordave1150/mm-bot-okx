# ทิศทางการพัฒนา bot-trade

วันที่: 2026-09-06

แผนงานจากคำขอให้จัดทิศทางใหม่ ไม่ใช่คำสั่งรันบอตหรือสิทธิ์เปิด network/trading และไม่เปลี่ยน risk limits, qualification gates หรือหลักฐานเดิม

## เป้าหมายที่เลือก

ทำ OKX Demo market-maker สำหรับ BTC/USDT:USDT ให้มีเส้นทางใช้งานหลักหนึ่งชุด สามารถจบ session และอธิบายผลจากหลักฐานได้ ก่อนลงทุนเพิ่มกับฟีเจอร์หรือ production

ใช้ successor stack เดิมเป็นฐาน ไม่ execute `bot trade.txt` ใหม่ทั้งก้อน ไฟล์นั้นเป็นรายการความสามารถระยะยาว ไม่ใช่ acceptance criteria ของรุ่นแรก

รุ่นแรกสำเร็จเมื่อผู้ใช้อธิบายได้ว่า: รันอะไร ด้วย configuration ใด เกิด orders/fills อะไร จบอย่างไร บัญชีตรงหรือไม่ และกำไรขาดทุนรวมต้นทุนเป็นเท่าไร ข้อมูลที่พิสูจน์ไม่ได้ต้องแสดง unknown และหยุดการดำเนินงานต่อที่ต้องอาศัยข้อมูลนั้น

## สถานะที่ตรวจพบเพื่อวางแผน

นี่เป็นการอ่านเอกสาร โค้ดบางส่วน และ local markers ไม่ใช่ fresh test run, account check หรือ audit ทุก campaign

- `market_maker/`, adapter/runtime, accounting, reconciliation และ tests มีอยู่แล้ว จึงมีฐานที่ควรใช้ต่อ
- มี runtime เก่าและ successor ร่วมกัน ตาม `ARCHITECTURE.md`; ไม่ใช้ `main.py` เป็นทางลัด
- `CURRENT_STATUS.md` reconciled ถึง 2026-09-05 และยังไม่ครอบคลุม artifacts วันที่ 2026-09-06 ที่พบ
- แผน `R2_STAGE_C_POST_FLATTEN_TERMINAL_IMPLEMENTATION_PLAN.md` ระบุ PLAN ONLY แต่ working tree ปัจจุบันมี failure helpers, supervisor และ tests แล้ว ต้องตรวจสิ่งที่ทำจริงก่อนนำแผนเก่ามาทำซ้ำ
- ใน `artifacts/r2_stage_c_execution/` พบ run `20260906T072916Z` และ `20260906T075800Z` มี completion files แต่ยังไม่ได้ตรวจ hashes/chain จึงยังไม่รับรองว่าผ่าน
- Run `20260906T081500Z` มีเฉพาะ interruption guard ในไฟล์ระดับบน การมี guard ไม่พิสูจน์ process/account state
- Run `20260906T143000Z` และ `20260906T151055Z` มี failure marker: `R2_STAGE_C_EXECUTION_FAILED_NOT_ACCEPTABLE`, phase `UNHANDLED_POST_GUARD_EXIT`, terminal authority=false
- Run `20260906T151055Z` มี supervision record ระบุ exit_code=1 ข้อมูลนี้ยังไม่ชี้ root cause และไม่พิสูจน์สถานะบัญชีปัจจุบัน
- Working tree มี source/test edits และไฟล์ใหม่ค้างอยู่ ต้องรักษาไว้และตรวจเป็น baseline ก่อนแก้เพิ่ม

ดังนั้นงานเร่งด่วนคือยืนยัน baseline และสาเหตุที่วงจร session จบไม่ครบ ไม่ใช่เพิ่มกลยุทธ์หรือเริ่ม campaign ถัดไป

## ขอบเขตรุ่นแรก

| ทำให้จบ | พักไว้ |
| --- | --- |
| ตลาดเดียว กลยุทธ์ successor ปัจจุบันและ risk boundary เดิม | exchange/ตลาด/กลยุทธ์เพิ่มเติม |
| market data → quotes → orders → fills → accounting → terminal reconciliation | regime detection และ capital scaling เพิ่มเติม |
| หยุดอย่างถูกต้องเมื่อ state คลุมเครือ และบล็อก successor | Optuna, validation/holdout และ production |
| เส้นทางรันหลักที่มีเอกสารและรายงานผลสั้นหนึ่งชุด | dashboard ใหม่ และ refactor ทั้ง repo |
| tests ของ lifecycle และ failure ที่สำคัญ | การสร้าง repair framework ใหม่โดยไม่มีปัญหาที่พิสูจน์ได้ |

การพักงานหมายถึงไม่ลงทุนเพิ่มในรอบนี้ ไม่ถอด safety controls หรือความสามารถที่ถูกใช้อยู่แล้ว และไม่ลบหลักฐาน/legacy เพื่อให้ repo ดูสะอาด

## ลำดับส่งมอบ

### A — ทำให้สถานะปัจจุบันเชื่อถือได้

งานถัดไปทันที เป็น local/offline:

1. บันทึก baseline ของ dirty source/tests และระบุ executor/worker/supervisor/continuation ที่เชื่อมกันจริง
2. ตรวจ markers, manifests, hashes และ exact chain ของ run ที่เกี่ยวข้อง โดยไม่ถือชื่อไฟล์ COMPLETED เป็นการผ่าน
3. ตรวจ sanitized logs ของ run ล่าสุดและย้อนถึงจุดเริ่มปัญหา แยก observed facts, hypothesis และ unknown; ไม่แสดง credentials/account identifiers
4. เทียบ implementation กับแผนซ่อมเดิม: done / partial / missing พร้อมหลักฐาน ห้ามสรุป done เพียงเพราะมี helper หรือ test ชื่อที่ตรงกัน
5. อัปเดต `CURRENT_STATUS.md` เป็นแหล่งสถานะปัจจุบันแห่งเดียว ระบุ candidate, run, blocker, evidence และ next action; เอกสารประวัติยังเก็บตามเดิม

เกณฑ์จบ: ระบุได้ว่า candidate ใดกำลังประเมิน หลักฐานใดรับรองได้ อะไรยัง unknown และมี blocker หลักหนึ่งเรื่องที่ทำซ้ำแบบ offline ได้ หรือระบุข้อมูลที่ขาดอย่างชัดเจน

### B — ปิดปัญหา lifecycle หนึ่งรอบ

ใช้ผล A เลือกสาเหตุจริง ไม่สมมติล่วงหน้าว่า flatten เป็น root cause

- ทำ deterministic reproduction ผ่าน executor/worker path ที่เกี่ยวข้อง
- ซ่อมเฉพาะสาเหตุและขอบเขตที่ต้องใช้ร่วมกัน ใช้โมดูลเดิมก่อนเพิ่มโมดูลรุ่นใหม่
- พิสูจน์ success path, exception หลัง dispatch, acknowledgement/account state unknown, process exit และการปฏิเสธ evidence/identity ที่ใช้ต่อไม่ได้
- หลัง halt ต้องไม่ dispatch mutation เพิ่ม; account truth ต้องไม่ถูกแทนด้วย local state หรือ default True
- รัน targeted tests และ regression scopes ที่ protocol กำหนด ภายใต้ blank credentials/socket denial; อัปเดต fingerprint หลัง source นิ่งตามข้อกำหนดเดิม

เกณฑ์จบ: reproduction เดิมผ่านด้วยพฤติกรรมที่ถูกต้อง พร้อมหลักฐานว่า failure ถูกบันทึกและไม่ถูกนำไปยอมรับเป็น success; required checks ผ่านจริง ไม่ใช่อาศัยผลเก่า

### C — ยืนยันวงจร Demo ที่จบครบ

หลัง A/B ผ่าน จัดเตรียมขอบเขต fresh preflight และ Demo run ที่ review ได้ตาม protocol และขอสิทธิ์ network/trading ที่จำเป็นก่อน execute ไม่ reuse failed/closed identities

เกณฑ์จบ: session ในขอบเขตที่ได้รับอนุญาตจบด้วย authoritative terminal position/orders 0/0, fill/fee/FIFO reconciliation ตรง, ไม่มี unresolved mutation และมี chain/hash ตรวจสอบได้ รวมถึง process outcome

การผ่าน session นี้เป็น operational milestone เท่านั้น ไม่เท่ากับผ่าน economic qualification หรือพร้อมใช้เงินจริง และไม่เปลี่ยน frozen session duration/schedule เพื่อให้ผ่านง่ายขึ้น

### D — ตัดสินความคุ้มค่าของกลยุทธ์

เมื่อระบบทำงานและบัญชีเชื่อถือได้แล้ว จึงประเมิน fresh campaign ตาม sample/session denominator และ gates ที่อนุมัติไว้ ห้ามนับ canary หรือ session ซ้ำเพื่อเติมจำนวน

รายงาน normal maker fills แยก bid/ask, FIFO round trips, normal net PnL, special/flatten net PnL, aggregate net PnL หลังต้นทุน, special-flatten sessions/eligible sessions และ risk breaches ผลไม่ครบต้องเป็น insufficient evidence

เกณฑ์จบ: ออกคำตัดสิน continue / redesign / insufficient evidence พร้อมเหตุผลตาม gates ที่มีอยู่ ผล Demo ที่ดีไม่ถือเป็นหลักฐานความสามารถทำกำไรบน Live โดยอัตโนมัติ

## กติกาป้องกันงานวน

- ทำ blocker หลักครั้งละหนึ่งเรื่อง ทุกงานต้องมี reproduction, expected behavior และเกณฑ์จบก่อนแก้
- นับความคืบหน้าจาก milestone ที่ผ่าน ไม่ใช่จำนวนไฟล์ tests หรือ artifacts ที่สร้าง
- สถานะของแต่ละช่วงมีเพียง not started / in progress / passed / blocked พร้อม evidence; ไม่ตั้งเปอร์เซ็นต์ความพร้อมจากการคาดเดา
- หนึ่งรอบ repair ต้องจบด้วยผลและการตัดสินใจ ถ้าซ่อมแล้วการยืนยันถัดไปล้มเหลวด้วย failure class เดิม ให้หยุดต่อ patch และทบทวน lifecycle design ก่อนเริ่มรอบใหม่
- คง stop rule ด้าน economic repair ที่มีอยู่ใน `BOT_TRADE_PRODUCTION_PROMOTION_PROCESS_MEMORY.md`; ตรวจว่าประวัติได้ถึงจุดนั้นแล้วหรือยังใน A ไม่รีเซ็ตจำนวนรอบด้วยการเปลี่ยนชื่อแผน
- ไม่เพิ่ม risk/time/create/retry budgets หรือลดเกณฑ์เพื่อให้ได้ผลผ่าน
- การย้ายไฟล์หรือสร้าง CLI ใหม่ทำเมื่อยืนยัน path หลักแล้วและแก้ pain point ที่ระบุได้ ไม่ตั้งเป็นโครงการใหญ่ก่อนรู้ root cause

## รูปแบบรายงานต่อผู้ใช้

รายงานทุกครั้งใน 5 ข้อสั้น ๆ:

1. หมุดหมายปัจจุบันและผลที่จบแล้ว
2. พฤติกรรมที่เปลี่ยนและเหตุผล
3. หลักฐาน/การทดสอบใหม่ที่รองรับ
4. blocker หรือ unknown ที่เหลือ
5. งานถัดไปหนึ่งเรื่อง และสิทธิ์เพิ่มเติมเฉพาะเมื่อข้ามขอบเขตจริง

## งานแรกที่พร้อมเริ่ม

ดำเนินการช่วง A ให้ครบ โดยใช้ `r2-stage-c-run-20260906T151055Z` เป็นจุดเริ่มตรวจย้อนกลับ ตรวจด้วยว่ามี run ใหม่กว่านี้ก่อนเริ่ม เก็บ working tree และ historical evidence เดิม ห้ามรัน Demo ระหว่างการตรวจนี้ ผลส่งมอบคือ CURRENT_STATUS ที่ reconcile แล้วและข้อสรุปว่าช่วง B ต้องซ่อมอะไร หรือ implementation ที่มีอยู่ผ่านการพิสูจน์แล้วหรือไม่

รอบจัดทิศทางนี้ส่งมอบแผนและลิงก์ใน README เท่านั้น ยังไม่ได้ทำช่วง A ครบ ไม่รับรองผล tests หรือ account state ปัจจุบัน
