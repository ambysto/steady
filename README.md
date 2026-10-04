# Ambysto Steady

*Stable Internet Connection* — sản phẩm của Ambysto ([ambysto.com](https://ambysto.com)). Tên nội bộ của repo, thư mục dữ liệu và package vẫn là `StableInternet`.

Công cụ chạy trên Windows giúp **giữ kết nối Internet ổn định**: theo dõi chất lượng đường truyền liên tục, tự khôi phục khi rớt mạng, và cung cấp các tối ưu hệ thống dạng **toggle bật/tắt có khôi phục** để thử nghiệm an toàn.

> Trạng thái: **bản Windows đang hoàn thiện** (monitor, chẩn đoán, tối ưu có khôi phục, watchdog, failover, giao diện desktop, bản đóng gói). Xem [docs/ROADMAP.md](docs/ROADMAP.md), [docs/OPEN-QUESTIONS.md](docs/OPEN-QUESTIONS.md) và [CHANGELOG.md](CHANGELOG.md).

## Vì sao cần tool này?

Phần mềm không làm sóng Wi‑Fi mạnh hơn, nhưng làm tốt 3 việc:

1. **Đo và ghi lại** — biết chính xác lỗi nằm ở PC, router hay nhà mạng (ISP), có số liệu để so sánh trước/sau mỗi thay đổi.
2. **Tự khôi phục** — phát hiện driver treo / mất kết nối và xử lý trong vài giây thay vì chờ Windows hoặc thao tác tay.
3. **Tối ưu có kiểm soát** — mỗi tinh chỉnh là một toggle, lưu giá trị gốc trước khi áp dụng, tắt là trả về như cũ.

## Tính năng dự kiến

| Nhóm | Mô tả |
|---|---|
| Tổng quan | Ping / mất gói / jitter realtime tới router và Internet, tín hiệu Wi‑Fi, lịch sử 24h |
| Tối ưu | Các toggle: power saving của card, PCIe ASPM, wake-on-LAN, TCP, IPv6… — xem [docs/TWEAKS.md](docs/TWEAKS.md) |
| Chẩn đoán | Driver & bản có thể rollback, nhiễu kênh, lịch sử rớt mạng, benchmark DNS… — xem [docs/DIAGNOSTICS.md](docs/DIAGNOSTICS.md) |
| Watchdog | Tự kết nối lại / khởi động lại card mạng khi phát hiện sự cố — xem [docs/WATCHDOG.md](docs/WATCHDOG.md) |
| Nhật ký | Mọi sự cố, thay đổi tối ưu và hành động của watchdog |

## Yêu cầu

- Windows 10/11
- Python 3.11+
- Quyền Administrator để áp dụng tối ưu (chế độ chỉ-đọc vẫn theo dõi được)

## Cấu trúc thư mục

```
StableInternet/
├── README.md             Tổng quan dự án (file này)
├── CHANGELOG.md          Lịch sử thay đổi theo phiên bản
├── CONTRIBUTING.md       Quy ước code, commit, thêm tweak mới
├── CLAUDE.md             Hướng dẫn cho AI agent làm việc trong repo
├── docs/
│   ├── ARCHITECTURE.md   Kiến trúc, module, API, luồng dữ liệu
│   ├── UI-DESIGN.md      Định hướng giao diện (bản nháp)
│   ├── TWEAKS.md         Danh mục toggle: giá trị, rủi ro, cách khôi phục
│   ├── DIAGNOSTICS.md    Danh mục kiểm tra chẩn đoán và ngưỡng đánh giá
│   ├── WATCHDOG.md       Logic tự khôi phục và các giới hạn an toàn
│   ├── SECURITY.md       Mô hình bảo mật (server chạy quyền Admin)
│   ├── BASELINE.md       Kết quả đo hiện trạng PC trước khi tối ưu
│   ├── EXPERIMENT-LOG.md Nhật ký các thay đổi cấu hình và kết quả quan sát
│   ├── ROADMAP.md        Kế hoạch theo giai đoạn
│   ├── OPEN-QUESTIONS.md Các điểm cần trao đổi / quyết định
│   └── adr/              Architecture Decision Records
├── app/                  Mã nguồn Python (backend)
├── web/                  Giao diện (HTML/CSS/JS)
├── scripts/              Script tiện ích (launcher, cài đặt)
├── tests/                Kiểm thử
└── data/                 Dữ liệu runtime (không commit): settings, backup, metrics
```

## Cách chạy

Mới có phần theo dõi (chỉ đọc, không cần Admin). Chạy monitor ở foreground, dừng bằng Ctrl+C:

```powershell
python -m app.monitor
```

**Bản đóng gói (không cần cài Python):** `pip install -r requirements.txt pyinstaller==6.22.3` rồi `python scripts/build.py [--smoke]` → `dist/Ambysto Steady/` và `dist/StableInternetConnection-<version>-win64.zip`. Người dùng giải nén, bấm đúp `Ambysto Steady.exe` → app hỏi cài cho người dùng hiện tại (không cần Admin): chép vào `%LOCALAPPDATA%\Programs`, monitor chạy cùng Windows, lối tắt Start menu + icon khay khi đăng nhập, có trong Apps & features. Gỡ từ Apps & features: khôi phục mọi tối ưu về giá trị gốc (UAC một lần) rồi xóa task, lối tắt, thư mục; hỏi có xóa số liệu không. Chưa ký số nên SmartScreen sẽ cảnh báo.

**Cửa sổ ứng dụng + icon khay:** `pip install -r requirements.txt` (pywebview, pystray, Pillow) rồi bấm đúp `scripts\desktop
un_desktop.pyw` (hoặc `pythonw -m app.desktop`, thêm `--minimized` để chỉ hiện ở khay). Đóng cửa sổ chỉ ẩn xuống khay; monitor chạy riêng và không bị tắt.

Số liệu ghi vào `data/metrics.db`. Monitor cũng mở **giao diện** tại `http://127.0.0.1:47613/` (chỉ máy này truy cập được; xem `docs/SECURITY.md`): Tổng quan, Tối ưu, Chẩn đoán, Nhật ký, Cài đặt — 9 ngôn ngữ, sáng/tối, co giãn tới khổ điện thoại. Chưa có chặn chạy hai bản cùng lúc (xem SIC-12), nên đừng chạy song song với `scripts/monitor/ping-logger.ps1` lâu dài vì sẽ ping gấp đôi.

Chạy test: `python -m unittest discover -s tests -t .`

## Tài liệu

Bắt đầu từ [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md). Mọi quyết định kiến trúc được ghi tại [docs/adr/](docs/adr/).

## Ký số và quyền riêng tư

- [Code signing policy](CODE_SIGNING.md) — ký số qua SignPath Foundation (đang chờ duyệt).
- [Privacy policy](PRIVACY.md) — không thu thập dữ liệu; liệt kê mọi kết nối app tạo ra.

## Giấy phép

Copyright (C) 2026 Ambysto. Phát hành theo **GNU General Public License v3.0**, xem [LICENSE](LICENSE). Đóng góp: xem [CONTRIBUTING.md](CONTRIBUTING.md) và [CLA.md](CLA.md).
