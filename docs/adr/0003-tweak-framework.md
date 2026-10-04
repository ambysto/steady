# ADR-0003: Khung tweak — sao lưu trước, kiểm chứng sau, rollback khi lỗi

- **Trạng thái:** Accepted
- **Ngày:** 2026-10-04

## Bối cảnh

Tweak ghi vào thuộc tính driver, registry HKLM, powercfg và binding của card mạng. Lỗi ở đây để lại cấu hình mà người dùng khó tự sửa. Máy phát triển đã từng được áp dụng tay (EXP-001) với bản sao lưu nằm ngoài tool (`data/manual/backup-*.json`). Yêu cầu: bật rồi tắt phải trả đúng giá trị gốc, sao lưu sống sót qua khởi động lại, lỗi giữa chừng phải tự rollback ([TWEAKS.md](../TWEAKS.md), [CONTRIBUTING.md](../../CONTRIBUTING.md)).

## Quyết định

1. **Tweak là dữ liệu khai báo trên 4 loại nguyên thủy**: thuộc tính nâng cao của card (`Set-NetAdapterAdvancedProperty`), DWORD registry HKLM, chỉ số powercfg AC/DC của power plan hiện tại, binding của card (`Enable/Disable-NetAdapterBinding`). Mọi thao tác hệ thống đi qua **một** lớp `System` (`app/winsys.py`): bản thật là `WindowsSystem`, test dùng bản giả. Chỉ `winsys.py` được ghi vào máy.
2. **Bật**: đọc trạng thái → kiểm tra hỗ trợ và quyền Admin → nếu chưa có sao lưu thì chụp giá trị gốc và **ghi `backup.json` xuống đĩa trước khi áp dụng** → áp dụng → **đọc lại để kiểm chứng**. Áp dụng lỗi hoặc không có hiệu lực ⇒ rollback về giá trị ngay trước khi áp dụng; rollback cũng lỗi ⇒ giữ sao lưu, ghi sự kiện `bad` để người dùng tắt lại sau.
3. **Không bao giờ ghi đè sao lưu đã có**: giữ giá trị gốc cũ nhất. `backup.json` hỏng ⇒ từ chối mọi thao tác ghi (thay vì coi như chưa có sao lưu rồi chụp nhầm giá trị đã bị đổi).
4. **Tắt khi có sao lưu**: khôi phục giá trị gốc, chụp lại và so khớp với gốc; chỉ xóa sao lưu khi khớp.
5. **Tắt khi không có sao lưu** (giá trị đã đổi trước khi có tool): chỉ dùng mặc định **đã biết chắc** — `Reset-NetAdapterAdvancedProperty` (mặc định của driver), xóa giá trị registry khi mặc định của Windows là "không đặt", bật lại binding. **powercfg không có mặc định đã biết** cho từng chỉ số ⇒ từ chối và giữ nguyên, không đoán.
6. **Đã bật sẵn** (tool không bật) ⇒ bật là no-op, không tạo sao lưu. Sao lưu từ nguồn ngoài (vd. bản tay EXP-001) được **nhận** qua `adopt_backup()`, không đổi gì trên máy, và không ghi đè sao lưu đã có.
7. Một khóa cho mọi thao tác ghi. Mỗi thay đổi ghi sự kiện (`tweak_enabled`, `tweak_disabled`, `tweak_failed`) và thời điểm thay đổi gần nhất, để watchdog không coi việc card khởi động lại do tweak là sự cố.
8. Đọc trạng thái (`list_states()`) không có tác dụng phụ và dùng chung bộ nhớ đệm trong một lần đọc. CLI mặc định chỉ in kế hoạch; phải có `--apply` mới ghi. Test không bao giờ chạy thao tác ghi thật.

## Hệ quả

- ✅ Thêm tweak mới = thêm một khai báo; logic sao lưu/rollback/kiểm chứng chỉ viết một lần.
- ✅ "Tắt" có thể trả lời "không khôi phục được an toàn" thay vì đoán sai.
- ⚠️ Tweak powercfg đã được bật trước khi có tool và không có sao lưu thì không tắt được qua tool — cần nhận sao lưu ngoài hoặc người dùng tự chỉnh.
- ⚠️ Kiểm chứng chỉ đọc lại giá trị đã cấu hình (registry/driver), không chứng minh driver đã áp dụng nó vào phần cứng (một số thay đổi cần khởi động lại card hoặc máy).
