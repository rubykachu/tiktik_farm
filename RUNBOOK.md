# RUNBOOK — vận hành farm_tiktik

Máy tính của bạn chính là máy chủ. Không có môi trường nào khác.

Mọi lệnh trong tài liệu này chạy trong **Terminal**, tại thư mục chứa mã nguồn.
Mở Terminal rồi gõ `cd` + khoảng trắng, kéo thả thư mục vào cửa sổ, bấm Enter.

---

## Phần 1 — Cài lần đầu

Làm một lần duy nhất cho mỗi máy. Khoảng 30 phút, phần lớn là chờ tải.

### Bước 1. Cài Docker Desktop

Tải tại <https://www.docker.com/products/docker-desktop/>, cài, mở lên.

Chờ đến khi biểu tượng con cá voi ở thanh trên cùng hết nhấp nháy. Kiểm tra:

```bash
docker ps
```

Ra một bảng trống là được. Báo lỗi `Cannot connect` nghĩa là Docker chưa khởi động xong.

### Bước 2. Cài cloudflared

```bash
brew install cloudflared
```

Chưa có `brew` thì cài trước theo <https://brew.sh>.

### Bước 3. Tạo file cấu hình

```bash
cp .env.example .env
```

### Bước 4. Đặt mật khẩu đăng nhập trang web

Thay `mat-khau-cua-ban` bằng mật khẩu bạn tự nghĩ. Đặt dài, đừng dùng `admin123`
— trang web sẽ mở ra Internet, có bot dò tự động.

```bash
htpasswd -bc .htpasswd admin 'mat-khau-cua-ban'
```

Ghi lại mật khẩu này. Không xem lại được, chỉ đặt lại được.

### Bước 5. Nối tên miền

Cần một tên miền đã trỏ NS về Cloudflare.

Đặt tên cho máy này (ví dụ máy đầu tiên là `farm1`) và tên miền con muốn dùng,
rồi chạy:

```bash
NAME=farm1 DOMAIN=farm1.tenmien.com make tunnel-create
```

Lệnh này tự làm 3 việc: đăng nhập Cloudflare (trình duyệt mở ra, chọn tên
miền của bạn), tạo tunnel tên `farm1`, trỏ `farm1.tenmien.com` vào tunnel đó,
rồi ghi sẵn `~/.cloudflared/config.yml`.

Cho tunnel tự chạy mỗi khi bật máy:

```bash
cloudflared service install
```

Báo lỗi quyền thì thêm `sudo` ở đầu.

### Bước 6. Khai báo tên miền cho hệ thống

Mở file `.env` bằng TextEdit. Sửa hai dòng này thành tên miền của bạn:

```
HOST=https://farm1.tenmien.com
PUBLIC_BASE_URL=https://farm1.tenmien.com
```

Hai dòng phải giống hệt nhau. `PUBLIC_BASE_URL` là gốc của link tải video gửi
qua Telegram — điền sai thì link gửi đi sẽ hỏng.

### Bước 7. Bật hệ thống

```bash
make start
```

Lần đầu mất 10–20 phút vì phải tải thư viện. Các lần sau nhanh hơn nhiều.

### Bước 8. Kiểm tra

Mở trình duyệt vào `https://farm1.tenmien.com`. Hiện ô đăng nhập → gõ `admin`
và mật khẩu ở Bước 4 → vào được trang quản lý là xong.

---

## Phần 2 — Dùng hằng ngày

| Việc cần làm | Lệnh |
|---|---|
| Bật hệ thống | `make start` |
| Tắt hệ thống | `make stop` |
| Khởi động lại | `make restart` |
| Xem đang chạy không | `make status` |
| Xem hệ thống đang làm gì | `make logs` (Ctrl-C để thoát) |
| Cập nhật bản mới | `make update` |
| Sao lưu | `make backup` |
| Chạy tunnel nền | `make tunnel-start` |
| Dừng tunnel | `make tunnel-stop` |
| Xem các tunnel đã tạo | `make tunnel-list` |

Quên lệnh thì gõ `make` là hiện lại danh sách.

### Sao lưu

Chạy `make backup` mỗi tuần một lần:

```bash
make backup
```

Nó tạo thư mục trong `backups/` chứa cơ sở dữ liệu, cookie các tài khoản, mật
khẩu web và **khoá tunnel**.

**Chép thư mục đó sang ổ cứng ngoài hoặc Google Drive.** Mất khoá tunnel thì
tên miền không dùng lại được, phải làm lại Bước 5 với mã tunnel mới.

---

## Phần 3 — Khi có sự cố

Trước tiên luôn thử:

```bash
make restart
```

Không đỡ thì tra bảng dưới.

| Hiện tượng | Nguyên nhân | Cách xử lý |
|---|---|---|
| Trang web không vào được, báo **Error 1033** | Tunnel không chạy | `make tunnel-start`. Nếu chạy được thì bước cài service ở Bước 5 chưa xong. |
| Trang web hiện **502** hoặc **503** | Hệ thống chưa bật | `make start` |
| Trang trắng, không có ô đăng nhập | Docker chưa khởi động | Mở Docker Desktop, chờ hết nhấp nháy, rồi `make start` |
| Quên mật khẩu web | — | Đặt lại: `htpasswd -bc .htpasswd admin 'mat-khau-moi'` rồi `make start` |
| Link tải video trong Telegram báo lỗi | Quá 24 giờ, video đã tự xoá | Bình thường. Video chỉ giữ 24 giờ. |
| Link tải video mở ra ô đăng nhập | `PUBLIC_BASE_URL` sai tên miền | Sửa `.env` cho khớp tên miền, rồi `make start` |
| Không tải được video YouTube | YouTube đổi cách chặn | `make update`. Vẫn lỗi thì báo người viết phần mềm. |
| Đăng TikTok báo lỗi cookie | Cookie hết hạn | Vào trang Tài khoản, nhập lại cookie cho tài khoản đó |
| Chạy `make start` báo lỗi lạ | Bản build hỏng | `make rebuild` |
| Vẫn không được | — | `make reset` (giữ nguyên dữ liệu) |

### Không tự làm khi chưa hỏi

- `make clean-db` — **xoá sạch** lịch đăng, lịch sử, tài khoản đã lưu.
- `cloudflared tunnel delete` — mất tên miền, phải làm lại Bước 5.
- Xoá thư mục `data/` — mất toàn bộ dữ liệu.

---

## Phần 4 — AI viết caption (không bắt buộc)

Bỏ qua phần này cũng được. Không cài thì caption dùng nguyên tiêu đề gốc của
video nguồn, mọi thứ khác chạy bình thường.

Muốn AI viết lại caption thì cài Ollama ngay trên máy này:

```bash
brew install ollama
ollama serve          # để cửa sổ này chạy, hoặc bật app Ollama
ollama pull llama3.2
```

Mở file `.env`, tìm hai dòng sau và **bỏ dấu `#`** ở đầu:

```
OLLAMA_MODEL=llama3.2:latest
OLLAMA_BASE_URL=http://host.docker.internal:11434
```

**Giữ nguyên `host.docker.internal`.** Đó là cách container gọi ngược về máy này.
Sửa thành địa chỉ máy khác trong mạng LAN sẽ bị chặn vì lý do an toàn, và hệ
thống sẽ không khởi động được.

Rồi:

```bash
make start
```

Cuối cùng vào web → **Cài đặt** → chuyển chế độ AI sang **Local (Ollama)**.

Muốn AI thật sự chạy cho một lịch đăng, mở lịch đó ra và làm cả hai việc:

1. Trong ô Caption, thêm `{ai_title}`
2. Mở mục "Viết lại caption bằng AI", bấm **Dùng mẫu**, rồi bấm **Thử nghiệm**
   để xem AI viết ra gì trước khi chạy thật

Thiếu một trong hai thì AI không được gọi.

| Hiện tượng | Cách xử lý |
|---|---|
| Bật xong mà caption vẫn là tiêu đề gốc | Lịch đăng chưa có `{ai_title}` hoặc chưa điền prompt |
| Sau khi sửa `.env`, web không vào được | `make logs` xem lỗi. Thường do sửa sai `OLLAMA_BASE_URL`. |
| AI trả về chậm hoặc lỗi | Kiểm tra Ollama còn chạy: `ollama list` |

---

## Phần 5 — Thêm máy thứ hai

Mỗi máy cần tên miền con riêng và tunnel riêng. Máy thứ hai làm lại Phần 1,
đổi `farm1` thành `farm2` ở mọi chỗ: tên tunnel, tên miền con, và cả hai dòng
trong `.env`.

### Cách A — người dùng máy mới tự làm

Họ chạy Phần 1 từ đầu trên máy của họ, kể cả Bước 5. Cần họ đăng nhập được vào
tài khoản Cloudflare đang giữ tên miền.

### Cách B — bạn chuẩn bị sẵn, họ chỉ việc chép (khuyên dùng)

Bạn tạo tunnel hộ từ máy đang có, rồi gửi họ khoá. Họ không cần tài khoản
Cloudflare.

Trên máy bạn — thay `farm2.tenmien.com` bằng tên miền con muốn dùng:

```bash
cloudflared tunnel create farm2
```

Ghi lại mã tunnel (UUID) trong dòng `Created tunnel farm2 with id ...`. Nó cũng
là tên file khoá vừa ghi ra trong `~/.cloudflared/`.

Trỏ tên miền vào tunnel đó. Bắt buộc dùng UUID, và bắt buộc có `--config` trỏ
vào file tạm — nếu không `cloudflared` sẽ đọc `~/.cloudflared/config.yml` của
máy bạn và trỏ tên miền mới vào tunnel của **máy bạn**, mà vẫn báo thành công:

```bash
printf 'tunnel: <UUID>\n' > /tmp/route.yml
cloudflared --config /tmp/route.yml tunnel route dns <UUID> farm2.tenmien.com
rm /tmp/route.yml
```

Dòng `Added CNAME ... tunnelID=` in ra phải đúng UUID của tunnel mới. Khác số
đó là đã trỏ nhầm; chạy lại lệnh trên, thêm `--overwrite-dns` ngay sau
`route dns`.

Gửi cho họ ba thứ:

1. File khoá `~/.cloudflared/<UUID>.json`. **Giữ bí mật** — ai có nó cũng dựng
   được tunnel mang tên miền đó. Chuyển bằng USB hoặc AirDrop, không gửi qua
   chat hay email.
2. Nội dung `config.yml` dưới đây.
3. File `config.txt` của bạn.

```yaml
tunnel: <UUID>
credentials-file: /Users/<tên-user-máy-họ>/.cloudflared/<UUID>.json

ingress:
  - hostname: farm2.tenmien.com
    service: http://127.0.0.1:2705
  - service: http_status:404
```

Họ làm Phần 1 nhưng **bỏ Bước 5**, thay bằng:

```bash
mkdir -p ~/.cloudflared
cp <UUID>.json ~/.cloudflared/
```

Rồi tạo `~/.cloudflared/config.yml` với nội dung trên, sửa `credentials-file`
cho đúng tên user máy họ — xem bằng lệnh `whoami`. Sau đó chạy tiếp Bước 6.

Họ **không** chạy `make tunnel-create`: lệnh đó tạo tunnel mới, và tên miền đã
trỏ sẵn sẽ không dùng được nữa.

### Hai điều bắt buộc

1. **Không chép file `.env` từ máy cũ sang.** `PUBLIC_BASE_URL` phải là tên miền
   của chính máy đó, nếu không link video gửi đi sẽ trỏ nhầm sang máy kia và
   không tải được. Mật khẩu web (`.htpasswd`) cũng đặt riêng, theo Bước 4.

2. **Không cho hai máy cùng chạy một kênh với cùng một tài khoản TikTok.** Mỗi
   máy ghi nhớ riêng nó đã đăng gì; hai máy cùng chạy sẽ đăng trùng lên cùng
   một tài khoản, dễ bị TikTok coi là spam. Chia theo kênh, hoặc chia theo tài khoản.

Muốn máy mới dùng lại cookie TikTok hoặc khoá LLM của máy cũ thì phải chép cả
`data/.secret_key` lẫn `data/CookiesDir/`. Thiếu `.secret_key` thì giải mã hỏng.
Không chép `data/tiktok.db` — đó là lịch sử đăng riêng của từng máy.
