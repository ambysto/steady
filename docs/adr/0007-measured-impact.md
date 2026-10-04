# ADR-0007: Đo hiệu quả của một thay đổi bằng số liệu monitor trước/sau

- **Trạng thái:** Accepted
- **Ngày:** 2026-10-04

## Bối cảnh

Các tool "tăng tốc mạng" (IObit Internet Booster, TCP Optimizer…) đổi hàng loạt giá trị hệ thống mà không chứng minh được gì. Monitor của ta chạy 24/7 và lưu số liệu theo phút, nên có thể cho người dùng thấy một thay đổi — bật một tweak, hay một việc làm bằng tay như xoay ăng-ten — có làm mạng tốt hơn không. Nguy cơ là đưa ra kết luận sai từ quá ít dữ liệu hoặc từ hai khoảng thời gian không so sánh được (ban ngày với ban đêm, có lúc máy ngủ).

## Quyết định

1. **Hai cửa sổ dài bằng nhau** quanh thời điểm thay đổi `t`: sau = `[t, min(bây giờ, t + 24h, lúc thay đổi bị hoàn tác))`, trước = cùng độ dài ngay trước `t`. Đủ 24h mỗi bên thì hai cửa sổ phủ cùng các giờ trong ngày; ngắn hơn thì kết quả gắn nhãn **sơ bộ**.
2. **Chỉ tính thời gian có giám sát:** số phút có số liệu ping. Khoảng máy ngủ/monitor dừng không được tính là tốt hay xấu; tần suất được quy về "mỗi ngày giám sát".
3. **Chỉ số:** số sự cố (`router_down`, `internet_down`) mỗi ngày, phút mất kết nối mỗi ngày, % mất gói tới router, % phút đường truyền sụp (Rx ≤ 30 Mbps kèm mất gói ≥ 5%, như kiểm tra #13).
4. **Ngưỡng tối thiểu:** cần ≥ 2 giờ giám sát mỗi bên, nếu không kết quả là `collecting` (hoặc `no_baseline` khi không có số liệu trước thay đổi). Một chỉ số chỉ được gọi là tốt hơn khi giảm ít nhất một nửa **và** giá trị trước đủ lớn để có ý nghĩa (≥ 3 sự cố, ≥ 5 phút mất kết nối, ≥ 1% mất gói, ≥ 5% phút sụp); xấu hơn theo luật đối xứng. Còn lại là "không khác biệt rõ".
5. **Kết luận chung:** `better` / `worse` / `mixed` / `no_change`. Luôn kèm câu nhắc rằng đây là **tương quan đo được, không phải chứng minh nhân quả** — router, giờ cao điểm, thiết bị khác cũng có thể là nguyên nhân.
6. Thời điểm thay đổi lấy từ nhật ký (`tweak_enabled` có `tweak_id`, `manual_step_done`); với tweak bật trước khi có nhật ký đó thì dùng `captured_at` trong `backup.json` (chỉ khi bản sao lưu do tool tự chụp).

## Hệ quả

- ✅ Người dùng thấy bằng chứng cụ thể ("sự cố: 18 → 2 mỗi ngày") thay vì lời hứa.
- ✅ Không cần thêm dữ liệu hay bảng mới: dùng `minute_stats`, `wifi_stats`, `events` sẵn có.
- ⚠️ Hai thay đổi gần nhau (< 24h) thì cửa sổ chồng lên nhau và không tách được tác động — giao diện nên nói rõ điều này.
- ⚠️ Mạng vốn đã ổn thì phần lớn kết quả sẽ là "không khác biệt rõ" — đó là đúng, không phải lỗi.
