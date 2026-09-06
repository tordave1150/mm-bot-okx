# R2 Stage C post-flatten terminal evidence — Implementation plan

สถานะ: PLAN ONLY — ยังไม่ได้ implement หรือพิสูจน์ repair ตามแผนนี้

## 1. Scope และหลักฐานอ้างอิง

- campaign_id: `r2-campaign-20260905T201023Z`
- continuation_package_id: `r2-current-stage-c-prep-20260905T201429Z`
- failed_session_id: `r2-session-20260905T201023Z-s02:p0:ae2e0d11`
- failed_stage_c_run_id: `r2-stage-c-run-20260905T203829Z`
- หลักฐาน: `artifacts/r2_stage_c_execution/r2-stage-c-run-20260905T203829Z/`

ดำเนินการได้เฉพาะ R0 offline ตาม authorization ที่มีแล้ว: อ่าน local evidence/logs, แก้ exception/finally handling และเพิ่ม targeted deterministic non-Optuna tests แบบ blank credentials/socket-denied ห้าม network, credential reads, account check, order dispatch, prepare/run R1/R2/R3 และ production

รักษาหลักฐานเดิมทั้งหมด ห้าม accept/retry/resume/reuse failed identity หรือเริ่ม Session 3 ไม่ปรับ frozen risk limits และ retry budgets

## 2. ข้อค้นพบและสิ่งที่ยังไม่ทราบ

ตรวจพบเพียง interruption guard กับ runtime state ไม่มี completion manifest หรือ terminal marker ใน run ที่อ้างอิง ประวัติ local state ล่าสุดระบุ flatten `SUBMITTED`, inventory 0 และ owned orders ว่าง แต่ไม่ใช่ authoritative terminal reconciliation

`execute_r2_stage_c` เขียน success marker ท้ายฟังก์ชัน แต่ยังไม่มี outer failure handler ครอบ execution และ terminal checkpoint การ throw ก่อนถึงจุดนั้นทำให้ไม่มี failure evidence

ยังไม่มี traceback/exit-code ที่ยืนยัน exception ของ run จริง จึงห้ามสรุปว่า failure เกิดจาก flatten reconciliation โดยแน่นอน ต้องแยก observed facts, hypotheses และผลจำลองในรายงาน

Guard ปัจจุบันเป็นเพียงหลักฐานเริ่มงาน ไม่ได้หยุด process ที่ยังทำงาน และไม่ใช่ terminal failure marker ฟังก์ชัน `_reject_unfinished_prior_stage_c_session` ตรวจเพียงการมีอยู่ของ completion file; ยังไม่พิสูจน์ status/hash/identity และไม่ block reuse session เดิมด้วย run stamp ใหม่ Tests เดิมสองรายการยังไม่จำลอง executor interruption จริง

ประวัติการติดตามเคยทิ้ง `exec_command.session_id` โดยส่งต่อเฉพาะ `output` ทำให้ตาม runner ต่อไม่ได้ การหายของ tool channel ไม่พิสูจน์ว่า process จบแล้ว

## 3. Implementation sequence

### A. เก็บ baseline แบบ read-only

1. ตรวจ applicable repository instructions และ dirty worktree; เก็บ existing edits
2. บันทึก SHA-256 ของ guard/runtime state และ predecessor manifests; ไม่คัดลอก account identifiers หรือ secrets ลงรายงาน
3. อ่าน source และ local logs ที่ตรง run; เก็บข้อจำกัดว่าไม่มี traceback เมื่อไม่พบจริง
4. ทำ sanitized fixture จากรูปแบบ state ที่พบ โดยใช้ synthetic IDs และแยก fixture ออกจาก live evidence

### B. ครอบ lifecycle ด้วย failure boundary

ไฟล์หลัก: `okx_demo_r2_stage_c_executor.py`

1. ครอบตั้งแต่หลังจอง run/identity ก่อน credential access จนจบ completion write ด้วย outer exception boundary; แยก helper สำหรับ failure evidence เพื่อทดสอบได้
2. ติดตาม phase เช่น ADMISSION, QUOTING, TERMINAL_CANCEL, FLATTEN_DISPATCH, POST_FLATTEN_READ, LEDGER_REPLAY, CHECKPOINT, EVIDENCE_WRITE และ active session identity
3. เมื่อเกิด Exception/KeyboardInterrupt/SystemExit ให้ latch execution halted ก่อนเขียนหลักฐานและ re-raise; error handler/finally ห้ามเรียก cancel/flatten/preflight หรือ network cleanup
4. ปิด mutation dispatch หลัง latch รวม callable ที่อาจถูกเก็บก่อน halt; ไม่ retry mutation ที่ส่งแล้วหรือไม่ทราบผล
5. finally ทำเฉพาะ local resource cleanup/log flush ไม่เขียน success และไม่กลบ exception เดิมหาก evidence write ล้มเหลว
6. Catchable interruption สร้าง failure marker ได้; hard kill/power loss ไม่รับประกันว่า Python handler ทำงานได้ ให้ guard ค้างและ successor ถูก block ห้ามอ้างว่าทุก process termination เขียน terminal ได้เสมอ

### C. Terminal failure evidence แบบ write-once

เสนอไฟล์ `terminal_failure_evidence.json`, `failure_completion_hashes.json`, `R2_STAGE_C_EXECUTION_FAILED.json` แยกจาก success artifacts

- schema version, UTC timestamp จาก clock จริง, run/campaign/session/predecessor identities, phase
- reason code และ exception type; sanitized stack frame locations ห้าม serialize exception text/request payload/locals ที่อาจมี secrets โดยตรง
- normal create/flatten/cancel dispatch counts จาก audited wrapper พร้อมความหมาย dispatch เทียบ acknowledgement; หากไม่มี authoritative count ให้ null/unknown ห้าม default 0
- mutation retry counters, operation identities หรือ hashes ที่ตรวจสอบได้ และ completeness flag
- SHA-256 ของ local state bytes ที่อ่านได้; state missing/corrupt/read error ต้องบันทึก unknown โดยไม่อ้าง reconciliation
- terminal_account_authoritative=false, reconciliation=false, decision=NOT_READY, accept/retry/resume/reuse/next_session=false

เขียนข้อมูลและ manifest ก่อน terminal marker ใช้ exclusive/write-once publication, flush/fsync ตาม filesystem รองรับ ป้องกัน overwrite เมื่อ handler ถูกเรียกซ้ำ ตรวจ path อยู่ภายใน output directory ห้ามแก้ artifact ของ failed historical run

Failure และ success พร้อมกันต้องถูก reject; หากเขียน failure ไม่สำเร็จ guard ยังบล็อกและ exit ต้องไม่เป็น success

### D. Admission และ identity consumption

ไฟล์หลัก: `okx_demo_r2_s02_s03_continuation.py` และ executor

1. ตรวจ failure marker ก่อน success; reject missing/malformed/default fields, wrong chain/session, manifest ว่างหรือ hash ผิด และ path traversal
2. successor ต้องมี terminal success ของ exact preceding session พร้อม verified hashes และ safety evidence 0/0, reconciliation=true, retries=0; การมีไฟล์ชื่อ COMPLETED อย่างเดียวไม่พอ
3. บล็อก session identity ที่เคยเริ่มแล้วแม้ผู้เรียกเปลี่ยน run stamp ใช้ atomic local identity claim ก่อน credential access และเก็บ consumed claim หลัง success/failure
4. ไม่ให้ run เก่าที่ไม่มี guard หลุดจากการตรวจ; unknown/missing predecessor evidence ต้อง fail-closed
5. รอบ offline นี้ไม่ backfill terminal success หรือ claim ว่า historical failed run ผ่านแล้ว

### E. Execution observation สำหรับรอบที่ได้รับสิทธิ์ในอนาคต

เก็บ full tool result โดยเฉพาะ `session_id`, `exit_code`, `output` และ poll ด้วย write_stdin สำหรับ process session; ใช้ functions.wait เฉพาะ functions.exec cell ที่ยังทำงานอยู่ เก็บ sanitized stdout/stderr และ exit status ลง durable local logs ไม่มี rerun เมื่อ channel หาย

ต้องยืนยัน process identity/สถานะก่อนรายงานว่าหยุด การทำ logging/monitoring ไม่เพิ่ม order permission และไม่หยุด process โดยอนุมานจาก stdout ที่ว่าง ข้อนี้เป็น runbook และ offline fixture เท่านั้น ไม่ launch Demo ในงานนี้

## 4. Required deterministic test matrix

| Scenario | Expected result |
| --- | --- |
| Exception ก่อน flatten dispatch | failure marker; flatten dispatch=0 ที่พิสูจน์ได้ |
| Flatten dispatch แล้ว response สูญหาย | dispatch=1, acknowledgement unknown; ห้าม retry |
| Exception หลัง flatten ใน account read | failure marker, authority=false, local-state hash ตรง |
| Ledger replay/checkpoint exception | failure marker และ reason/phase ถูกต้อง |
| KeyboardInterrupt/SystemExit | failure evidence และ re-raise; ไม่มี next session |
| State missing/corrupt หรือ write failure | unknown fields; guard retained; ไม่มี success |
| Failure writer ถูกเรียกซ้ำ | ไม่ overwrite evidence เดิม |
| Malicious exception text | ไม่มี credential/request-secret sentinel ใน artifacts/logs |
| Saved mutation callable หลัง halt | dispatch ถูก block ก่อน raw exchange |
| Same session ID กับ new run stamp | reject ก่อน credential/network |
| Forged success file, wrong hash/identity, failure+success | successor rejected |
| Valid converged synthetic success | success hashes ผ่าน ไม่มี false failure |
| Hard-killed fixture child | ไม่มีการรับประกัน failure marker; guard บล็อก successor |

ใช้ fake exchange พร้อม mutation call log เพื่อพิสูจน์ counts/ordering ไม่ใช้เพียง assert boolean ใน marker จำลอง handler ผ่าน executor จริงอย่างน้อย post-flatten read และ checkpoint failure

รัน targeted interruption, continuation, evidence-integrity และ executor tests ที่เกี่ยวข้องภายใต้ explicit socket guard และ blank credential env; ป้องกัน dotenv read ด้วย test hook ตรวจ network attempts=0 อย่าอ้าง socket-denied เพียงเพราะคำสั่ง pytest สำเร็จ

## 5. Fingerprint และ R0 handoff

หลัง source changes จบ ตรวจ source coverage ของ failure/admission helpers แล้วคำนวณ candidate fingerprint ครั้งสุดท้าย อัปเดตเฉพาะ active fingerprint references ที่จำเป็นอย่างสอดคล้อง ไม่แก้ historical packages หรือผ่อน mismatch gate

สร้าง fresh non-overwriting R0 evidence ID ด้วย UTC timestamp จริง รายงาน source hashes, original evidence hashes, actual test commands/results, blocked sockets, known limitations และ requirement matrix ห้ามประกาศ PASSED หากยังขาด integration proof หรือเก็บ mutation counts ไม่ครบ

R0 นี้พิสูจน์ code behavior ใน fixture เท่านั้น ไม่พิสูจน์สถานะบัญชีปัจจุบันหรือทำให้ failed Session 2 acceptable อย่าใช้ชื่อ ID ที่ดูเป็นเวลาท้องถิ่นแล้วลงท้าย Z

## 6. Definition of done และ stop boundary

- Catchable exception หลัง flatten สร้าง write-once failure evidence ครบก่อน re-raise และไม่มี mutation เพิ่ม
- Prior failure/missing evidence และ consumed session identity ถูกบล็อกก่อน credentials/network
- Targeted deterministic tests ผ่านจริงภายใต้ guard พร้อม preserved historical evidence
- Frozen risk/retry budgets ไม่เปลี่ยน; สรุป traceback ของ run เดิมเป็น unknown ถ้ายังไม่มีหลักฐาน
- ส่ง fresh R0 evidence พร้อมผลและข้อจำกัด แล้วหยุดก่อน preparation/execution ของ R1/R2/R3 ตาม authorization ปัจจุบัน

ไม่ต้องขออนุมัติ R0 repair ซ้ำเพื่อทำแผนนี้ให้เสร็จ ส่วน fresh network chain ต้องได้รับสิทธิ์แยกตามขอบเขตที่ผู้ใช้กำหนด
