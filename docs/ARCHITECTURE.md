# Kiến trúc

> Bản thiết kế sơ bộ — sẽ cập nhật theo các quyết định trong [OPEN-QUESTIONS.md](OPEN-QUESTIONS.md).

## Tổng quan

```
┌──────────────────────────┐        HTTP + token        ┌────────────────────────────────┐
│  Giao diện (web/)         │ ─────────────────────────▶ │  Backend Python (app/)          │
│  Tổng quan · Tối ưu ·     │ ◀───────── JSON ────────── │  chạy quyền Admin, 127.0.0.1    │
│  Chẩn đoán · Nhật ký      │                            │                                │
└──────────────────────────┘                            │  server ─┬─ monitor ── watchdog │
                                                        │          ├─ tweaks              │
                                                        │          ├─ diagnostics         │
                                                        │          └─ storage (SQLite)    │
                                                        └───────────────┬────────────────┘
                                                                        │ ICMP (ctypes), DNS (UDP),
                                                                        │ PowerShell, netsh, powercfg,
                                                                        ▼ registry (winreg)
                                                                     Windows
```

## Module backend (`app/`)

| Module | Trách nhiệm |
|---|---|
| `config.py` | Đường dẫn, `settings.json`, `backup.json` (giá trị gốc trước khi tweak) |
| `winutil.py` | Helper gọi PowerShell (`-EncodedCommand`, trả JSON), `netsh`, kiểm tra Admin, phát hiện card Wi‑Fi và đường mạng chính (uplink) |
| `icmp.py` | Ping qua `IcmpSendEcho` (iphlpapi, ctypes) — không cần Admin, không tạo process |
| `dnsprobe.py` | Truy vấn DNS UDP tự dựng gói tin để benchmark từng DNS server |
| `storage.py` | SQLite: thống kê theo phút, tín hiệu Wi‑Fi, sự kiện; dọn dữ liệu cũ |
| `monitor.py` | Luồng ping từng mục tiêu, đọc trạng thái Wi‑Fi, phát hiện sự cố, gộp thống kê theo phút |
| `watchdog.py` | Tự khôi phục khi sự cố kéo dài — xem [WATCHDOG.md](WATCHDOG.md) |
| `tweaks.py` | Danh mục toggle và khung `read / capture / apply / restore` — xem [TWEAKS.md](TWEAKS.md) |
| `diagnostics.py` | Các kiểm tra chẩn đoán — xem [DIAGNOSTICS.md](DIAGNOSTICS.md) |
| `actions.py` | Thao tác một lần: kết nối lại Wi‑Fi, khởi động lại card, flush DNS, renew DHCP |
| `probe.py` | Probe TCP:443 / HTTP 204, không phụ thuộc ICMP — xác nhận "có Internet" khi ping bị hạn chế |
| `notify.py` | Toast Windows khi mất mạng (sau 30s) / watchdog can thiệp, tự tắt, gặp lỗi; giới hạn tần suất; chạy nền |
| `singleton.py` | Chặn chạy hai monitor cùng thư mục dữ liệu (mutex có tên) |
| `autostart.py` | Task Scheduler khởi động monitor lúc đăng nhập (tạo từ XML) |
| `winsys.py` | Lớp duy nhất được ghi vào máy (thuộc tính card, HKLM, powercfg, binding) |
| `elevated.py`, `elevation.py` | Tiến trình con chạy quyền Admin qua UAC cho thao tác ghi — ADR-0005 |
| `server.py` | HTTP server, định tuyến API, phục vụ `web/` |
| `desktop.py` | Vỏ desktop (ADR-0002): cửa sổ pywebview dùng khung gốc của Windows (Snap Layouts, đổi kích thước, thanh tiêu đề tô cùng màu nền trang) quanh giao diện + icon khay (pystray). Chỉ là trình xem: đóng cửa sổ = ẩn xuống khay, "Thoát" chỉ tắt vỏ, monitor vẫn chạy. Chỉ module này cần `requirements.txt` |
| `failover.py` | Chuyển sang đường mạng dự phòng ([ADR-0008](adr/0008-failover.md)): dò các đường (card vật lý có default route), đo từng đường bằng TCP bind vào IP của nó, chính sách thuần có giới hạn an toàn, đổi InterfaceMetric có sao lưu trong `backup.json`. Mặc định tắt |
| `runtime.py` | Cách khởi chạy từng phần (monitor, desktop, elevated) từ mã nguồn hay từ bản đóng gói — task, UAC, lối tắt đều hỏi ở đây |
| `installer.py`, `entry.py` | Bản đóng gói `Ambysto Steady.exe` (PyInstaller, kèm Python): lệnh con `monitor`/`desktop`/`elevated`/`install`/`uninstall`/`diagnostics`; cài theo người dùng vào `%LOCALAPPDATA%\Programs`, gỡ thì khôi phục mọi tweak + metric trước. Dữ liệu của bản đóng gói ở `%LOCALAPPDATA%\StableInternet\data` |
| `impact.py` | Đo hiệu quả một thay đổi (tweak, việc làm bằng tay) bằng số liệu monitor trước/sau — [ADR-0007](adr/0007-measured-impact.md) |
| `suggestions.py` | Gợi ý từ lần chẩn đoán gần nhất: tweak + việc làm bằng tay (xoay ăng-ten, tắt Wi‑Fi modem…), "Tôi đã làm" |
| `dnswatch.py` | Phát hiện DNS bị đổi trên cùng mạng (chỉ đọc), cảnh báo bằng sự kiện + toast |
| `i18n.py`, `locales/*.json` | Bản dịch (ADR-0006): `t(key, **params)`, `msg()` để lưu thông điệp dịch sau, `format_duration()`; tiếng Anh là chuẩn và dự phòng |

## Mục tiêu theo dõi

| Tên | Địa chỉ | Ý nghĩa |
|---|---|---|
| `router` | Gateway của uplink hiện tại (tự phát hiện, làm mới 30s) | Lỗi ở đây ⇒ vấn đề Wi‑Fi / card / router |
| `cloudflare` | 1.1.1.1 | Internet |
| `google` | 8.8.8.8 | Internet (đối chứng) |
| `tcp_cloudflare`, `tcp_google` | 1.1.1.1:443, 8.8.8.8:443 | Probe TCP (không dùng ICMP), mỗi 10s |
| `http_cloudflare` | `http://cp.cloudflare.com/generate_204` | Probe HTTP, phải trả đúng 204 (200/chuyển hướng ⇒ nghi captive portal), mỗi 10s |

Probe cấu hình ở `settings.json` → `probes` (tắt bằng `"enabled": false`).

DNS của đường mạng chính được đọc mỗi 60 giây bằng `GetAdaptersAddresses` (ctypes, không tạo process). "Mạng" = (giao diện, gateway, SSID): sang mạng mới thì ghi `dns_observed`; cùng mạng mà DNS khác (thấy 2 lần liên tiếp) thì ghi `dns_changed` (warn) và bật toast. Tắt bằng `settings.json` → `dns_watch.enabled`. Số liệu probe nằm cùng bảng `minute_stats`, `target` là tên probe, `ip` là địa chỉ/URL.

Phân loại sự cố:
- Router không phản hồi ≥ 3 lần liên tiếp ⇒ **mất kết nối nội bộ** (PC ↔ router).
- Router OK nhưng **cả** mọi mục tiêu ping **và** vòng probe gần nhất đều thất bại ≥ 3 lần liên tiếp ⇒ **mất Internet** (router ↔ ISP). Chỉ ping hỏng mà probe vẫn thông thì **không** phải sự cố (ICMP là thứ đầu tiên bị hạn chế); vòng probe cũ quá 2,5 chu kỳ không được tính (quay về chỉ dùng ping).

## Lưu trữ (`data/`)

| File | Nội dung |
|---|---|
| `settings.json` | Cấu hình người dùng (watchdog, chu kỳ ping, mục tiêu, `ui.language`…) |
| `backup.json` | Giá trị gốc của từng tweak trước khi áp dụng |
| `metrics.db` | SQLite (WAL, `PRAGMA user_version` = phiên bản schema): `minute_stats(ts, target, ip, sent, lost, avg, max, jitter)`, `wifi_stats(ts, state, ssid, bssid, channel, signal, rssi, rx_mbps, tx_mbps)`, `events(id, ts, kind, level, message, duration, message_key, message_params)` (v3: sự kiện có lời văn lưu khóa dịch + tham số JSON, cột `message` giữ bản tiếng Anh để đọc thẳng DB; API trả chữ theo ngôn ngữ đang chọn). `ts` là Unix giây UTC; bảng thống kê khoá theo đầu phút. Sự kiện `*_down` ghi lúc kết thúc sự cố nên `ts` là thời điểm phục hồi, `duration` cho biết lúc bắt đầu. Khi monitor mất số liệu > 30 s (máy ngủ/treo), sự cố đang mở được cắt tại tick cuối trước khoảng trống (message có "cut short by a monitoring gap") và một sự kiện `monitor_gap` ghi độ dài khoảng trống — thời gian không đo được không bao giờ bị tính là mất mạng. `rx_mbps`/`tx_mbps`/`bssid` cần cho chẩn đoán MLO và roaming; `ip` vì IP router có thể đổi |

Mẫu ping thô chỉ giữ trong RAM (15 phút gần nhất); DB lưu thống kê theo phút, giữ 30 ngày.

## Giao diện (`web/`)

HTML/CSS/JS thuần, không thư viện, không bước build (ES module). Server phục vụ `web/` cùng origin; trang chỉ nạp script `type="module"` từ chính nó nên giữ được CSP chặt (`script-src 'self'`, không inline script, không `innerHTML` — test kiểm).

| File | Vai trò |
|---|---|
| `index.html` | Khung: thanh bên/thanh tab, chỗ chứa 5 màn hình, sheet xác nhận, icon SVG; token X-Token nhúng ở `<meta name="si-token">` |
| `tokens.css`, `app.css` | Design token (SIC-27) và component; < 760px thanh bên thành thanh tab |
| `js/app.js` | Điều hướng `#/màn-hình`, trạng thái chung, polling (Tổng quan 2s, khác 10–30s, dừng khi tab ẩn), theme, đổi ngôn ngữ |
| `js/api.js` | Gọi API kèm token, chờ job (`/api/jobs/{id}`), tự tải lại trang khi server khởi động lại (token mới) |
| `js/i18n.js` | Catalog từ `/api/i18n`, cùng luật với `app/i18n.py` (số nhiều, dấu phẩy thập phân) |
| `js/chart.js` | Biểu đồ SVG: router/Internet, mất gói, vùng sự cố, mốc thay đổi (tweak, việc làm bằng tay) |
| `js/widgets.js` | Khối "Hiệu quả", dòng gợi ý, bật/tắt tweak (sheet cho tweak thử nghiệm, UAC khi không có Admin) |
| `js/screens/*.js` | Tổng quan, Tối ưu, Chẩn đoán, Nhật ký, Cài đặt |

Chữ do backend sinh (chẩn đoán, sự kiện, tweak) đến từ API đã dịch; JS chỉ dịch nhãn của chính giao diện (`ui.*`).

## API

| Method | Đường dẫn | Mô tả |
|---|---|---|
| GET | `/api/state` | Quyền Admin, phiên bản, mục tiêu ping, các mục cài đặt sửa được (`watchdog`, `notify`, `ui`) |
| GET | `/api/i18n[?lang=vi]` | Ngôn ngữ đã chọn (`setting`), ngôn ngữ thực dùng, danh sách ngôn ngữ có catalog, toàn bộ bản dịch (đã trộn dự phòng tiếng Anh); `?lang=` để xem trước |
| GET | `/api/suggestions` | Gợi ý (tweak + việc làm bằng tay) từ lần chẩn đoán gần nhất, kèm kết quả trước/sau cho việc đã làm |
| POST | `/api/manual/{id}/done` | Người dùng đã làm một việc bằng tay → sự kiện `manual_step_done`, bắt đầu đo |
| GET | `/api/failover` | Các đường mạng, đường đang dùng, đã chuyển chưa, cài đặt |
| POST | `/api/failover/prefer/{ifIndex}` · `/api/failover/restore` | Chuyển sang / về (job; không có Admin thì qua UAC) |
| GET | `/api/impact` | Các thay đổi 14 ngày qua (tweak bật, việc đã làm) với số liệu trước/sau |
| GET | `/api/autostart` | Monitor có tự chạy cùng Windows không (chỉ đọc, cache 60s) |
| GET | `/api/live?window=300` | Mẫu ping gần nhất (≤ 900s), trạng thái Wi‑Fi, sự cố đang diễn ra |
| GET | `/api/history?hours=24&bucket=1` | Thống kê theo phút + Wi‑Fi + sự kiện (≤ 30 ngày); `bucket=N` gộp N phút (biểu đồ 7 ngày) |
| GET | `/api/events?limit=200` | Nhật ký |
| GET | `/api/tweaks` | Trạng thái tweak từ bộ đệm 60s; đọc lại chạy nền (~11s); `impact` cho tweak đang bật |
| POST | `/api/tweaks/{id}` | `{ "enable": true/false }` → job; không có quyền Admin thì qua UAC |
| POST | `/api/diagnostics` | Chạy toàn bộ chẩn đoán → job (một lần chạy tại một thời điểm) |
| GET | `/api/diagnostics/runs[/{id}]` | Các lần chẩn đoán đã lưu |
| GET | `/api/jobs/{id}` | Trạng thái/kết quả của job |
| POST | `/api/actions/{name}` | `reconnect`, `restart_adapter` (UAC), `flush_dns`, `renew_dhcp` → job |
| POST | `/api/settings` | Chỉ các khóa trong `SETTINGS_SCHEMA` (`watchdog.*`, `notify.enabled`, `ui.language`), kiểm kiểu, khoảng giá trị / danh sách cho phép |

Server chạy trong tiến trình monitor, **quyền thường**, tại `http://127.0.0.1:47613/` (đổi trong `settings.json` → `server.port`); vị trí thực tế ghi ở `%LOCALAPPDATA%\StableInternet\server.json`. Mọi request phải có `Host` đúng; mọi `/api/*` yêu cầu header `X-Token`; request ghi bị từ chối nếu khác origin — xem [SECURITY.md](SECURITY.md) và [ADR-0005](adr/0005-unelevated-server-uac-writes.md).

## Luồng bật một tweak

```
UI bật toggle → POST /api/tweaks/{id} {enable:true}
  → kiểm tra supported + quyền Admin
  → nếu chưa có backup: capture() giá trị gốc → backup.json
  → apply()
  → ghi sự kiện "Bật tối ưu: …" vào nhật ký
  → read() lại và trả trạng thái mới cho UI
```

Tắt: `restore(backup)` → xóa backup → ghi sự kiện. Nếu không có backup (giá trị đã được đổi từ trước khi dùng tool), khôi phục về mặc định của driver/Windows.
