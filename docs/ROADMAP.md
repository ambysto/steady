# Lộ trình

## Giai đoạn 0 — Thiết kế *(đang thực hiện)*
- [x] Đo hiện trạng máy phát triển → [BASELINE.md](BASELINE.md)
- [x] Bộ tài liệu thiết kế ban đầu
- [ ] Chốt các câu hỏi mở → [OPEN-QUESTIONS.md](OPEN-QUESTIONS.md)
- [ ] Chốt định hướng giao diện → [UI-DESIGN.md](UI-DESIGN.md)

## Giai đoạn 1 — Theo dõi (chỉ đọc)
- [x] `icmp`, `dnsprobe`, `winutil`
- [x] `monitor` + `storage` (thống kê theo phút, sự kiện)
- [x] Chặn chạy hai bản cùng lúc; tự khởi động bằng Task Scheduler (đã cài trên máy phát triển 2026-10-04, monitor chạy từ 09:59)
- [ ] Server + tab Tổng quan, Nhật ký
- [ ] Chạy 1–2 ngày để có số liệu "trước tối ưu"

## Giai đoạn 2 — Chẩn đoán
- [x] Toàn bộ kiểm tra trong [DIAGNOSTICS.md](DIAGNOSTICS.md) (`python -m app.diagnostics`; kiểm tra #10 chờ khung tweak)
- [x] Lưu kết quả các lần chẩn đoán để so sánh (`--compare`)

## Giai đoạn 3 — Tối ưu (toggle)
- [ ] Khung tweak + backup/restore
- [ ] Các tweak rủi ro `low`
- [ ] Các tweak `medium` / `experimental`
- [ ] Đánh dấu thời điểm bật/tắt tweak trên biểu đồ để so sánh trước/sau

## Giai đoạn 4 — Watchdog & tiện ích
- [ ] Watchdog theo [WATCHDOG.md](WATCHDOG.md)
- [ ] Khởi động cùng Windows
- [ ] Thông báo (toast) khi mất mạng / watchdog can thiệp

## Ý tưởng sau này
- Failover sang đường dự phòng (USB 4G, điện thoại tethering, mạng thứ hai)
- Báo cáo tuần (uptime, số lần rớt, thời gian mất mạng)
- Xuất số liệu CSV để làm việc với nhà mạng
