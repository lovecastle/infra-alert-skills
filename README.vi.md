# infra-alert-skills

[English](README.md) · **Tiếng Việt** · [简体中文](README.zh-CN.md)

Bộ skill để Claude tự cài đặt và vận hành việc giám sát cho một hạ tầng nhỏ, rồi báo vào chat khi có sự cố và khi
sự cố đã hết: vài VPS, một máy GPU, các dịch vụ chạy sau Cloudflare. Cảnh báo gửi về Telegram, hoặc WeCom,
Feishu/Lark, DingTalk, Bark, ntfy, ServerChan, PushPlus, hay bất kỳ webhook nào.

Bạn nói muốn theo dõi cái gì. Claude xem máy chủ, đề xuất những thứ nên theo dõi, cài một file monitor duy nhất,
chứng minh cảnh báo thật sự tới nơi, và về sau giúp bạn tìm ra nguyên nhân của từng cảnh báo.

```
[infrawatch] 🔴 vps-01: 1 problem, 1 resolved
🔴 disk /: 96% used, 1.8 GB free
✅ http api: HTTP 200 (after 12m)
```

Bộ skill này bắt nguồn từ cách chúng tôi đang giám sát [allshorts.net](https://allshorts.net) (vài máy chủ, một
máy GPU và Cloudflare, tất cả báo về một nhóm chat Telegram), sau đó được viết lại cho mục đích dùng chung sau khi
tìm hiểu cách Uptime Kuma, Gatus, Healthchecks, 哪吒 Nezha, Beszel và nhiều dự án khác giải quyết cùng bài toán.
Phương án bảo mật, chặn crawl và cảnh báo của chúng tôi nằm ở [docs/approach.md](docs/approach.md) (tiếng Anh);
những gì học được từ từng dự án nằm ở [docs/prior-art.md](docs/prior-art.md).

## Có gì bên trong

| Skill | Claude dùng nó để làm gì |
|---|---|
| [`setup-alert-channel`](skills/setup-alert-channel/SKILL.md) | Hướng dẫn bạn tạo bot Telegram và kênh hoặc nhóm nhận thông báo, tìm chat id, cất token an toàn, gửi tin thử. Có thêm tám kênh khác cho nơi không dùng được Telegram |
| [`monitor-hosts`](skills/monitor-hosts/SKILL.md) | Cài `infrawatch.py` lên máy chủ Linux qua SSH: ổ đĩa, bộ nhớ, tải CPU, dịch vụ systemd, container Docker, endpoint HTTP/TCP, chứng chỉ TLS, GPU NVIDIA, khởi động lại, OOM kill, và tuỳ chọn theo dõi bảo mật |
| [`monitor-cloudflare`](skills/monitor-cloudflare/SKILL.md) | Triển khai một Cloudflare Worker (gói miễn phí) hỏi các URL của bạn mỗi phút từ bên ngoài, và báo khi một máy chủ ngừng gửi heartbeat |
| [`triage-alerts`](skills/triage-alerts/SKILL.md) | Nhận một cảnh báo, tìm nguyên nhân bằng các lệnh chỉ đọc, đề xuất cách sửa, và chỉnh những cảnh báo gây nhiễu |

Hai thành phần, dùng riêng cũng được, dùng chung thì chắc hơn:

- **`infrawatch.py`** trên từng máy: một file Python, chỉ dùng thư viện chuẩn, chạy bằng systemd timer mỗi phút.
  Không daemon, không database, không cần cài gói nào.
- **Worker** ở ngoài máy chủ: thấy được điều mà một máy đã tắt không thể tự báo. Các máy chủ kiểm tra ngược lại
  Worker, nên không bên nào chết mà không ai biết.

Sơ đồ kiến trúc: xem [README tiếng Anh](README.md#what-is-in-it).

## Cài đặt

Trong Claude Code:

```
/plugin marketplace add lovecastle/infra-alert-skills
/plugin install infra-alerts@infra-alert-skills
```

Hoặc dùng [skills CLI](https://github.com/vercel-labs/skills), cho Claude Code và các agent khác đọc được `SKILL.md`:

```bash
npx skills add lovecastle/infra-alert-skills
```

Các skill cần một agent có shell (chúng dùng `ssh`, `scp`, `curl`, `npx wrangler`). Trong ứng dụng Claude không có
shell, `setup-alert-channel` vẫn dùng được như một bản hướng dẫn: nén thư mục skill thành zip rồi tải lên ở
Customize → Skills.

## Cách dùng

Cứ nói theo cách của bạn, bằng tiếng Việt cũng được. Ví dụ:

| Bạn muốn | Hãy nói |
|---|---|
| Có bot và kênh Telegram nhận cảnh báo | "Tạo giúp tôi kênh Telegram để nhận cảnh báo server" |
| Theo dõi một máy chủ | "Cài giám sát lên `vps-01` (ssh alias của tôi) và báo về Telegram khi có vấn đề" |
| Theo dõi máy GPU | "Máy `rtx` chạy dịch vụ inference trên card RTX. Theo dõi cả GPU và báo tôi nếu job chết" |
| Biết khi server hoặc website sập hẳn | "Triển khai watcher trên Cloudflare: kiểm tra https://example.com và báo nếu vps-01 hoặc vps-02 im lặng" |
| Hiểu một cảnh báo | "Tôi vừa nhận được: `[infrawatch] 🔴 vps-01: memory: 97% used`. Chuyện gì đang xảy ra?" |
| Yên lặng trong lúc deploy | "Tắt cảnh báo trên vps-01 trong một tiếng" |

Claude luôn hỏi trước khi thay đổi bất cứ thứ gì trên máy chủ hay tạo tài nguyên trong tài khoản Cloudflare của
bạn, và cho bạn xem một lần chạy thử (`infrawatch.py check`) trước khi bật giám sát.

## Tạo bot và kênh Telegram nhận thông báo

Skill `setup-alert-channel` dẫn bạn đi từng bước. Tóm tắt (mất khoảng năm phút, làm trong ứng dụng Telegram):

1. **Tạo bot.** Tìm **@BotFather** (có dấu tích xanh) → `/newbot` → đặt tên hiển thị → đặt username kết thúc
   bằng `bot` (ví dụ `acme_alerts_bot`). BotFather trả về **token** dạng `1234567890:AAH…`. Đây là mật khẩu của
   bot: không đưa vào git, không dán ở nơi công khai. Lỡ lộ thì gửi `/revoke` cho BotFather để đổi token mới.
2. **Chọn nơi nhận.**
   - Một người: mở bot và bấm **Start** (bot không thể nhắn cho người chưa bấm Start).
   - Cả nhóm, có trao đổi: tạo **nhóm**, thêm bot vào, gửi `/start@username_cua_bot`.
   - Cả nhóm, chỉ đọc (khuyên dùng): tạo **kênh riêng tư** → Quản trị viên → thêm bot làm admin với quyền
     **Đăng bài** → đăng một tin bất kỳ trong kênh.
   - Nhiều máy, mỗi máy một luồng: bật **Chủ đề (Topics)** cho nhóm và tạo mỗi máy một chủ đề.
3. **Lấy chat id.** Sau khi đã có một tin nhắn trong chat đó, Claude gọi `getUpdates` và đọc `chat.id`. Chat riêng
   là số dương, nhóm là số âm, supergroup và kênh bắt đầu bằng `-100`.
4. **Gửi tin thử** và xác nhận bạn thấy nó trong chat, có bật thông báo.

Token và chat id được cất ở một nơi duy nhất trên máy bạn: `~/.infra-alerts/notify.env` (quyền 600). Chi tiết,
giới hạn của Telegram và bảng xử lý lỗi (401, 403, đổi chat id khi nhóm thành supergroup, …):
[telegram.md](skills/setup-alert-channel/references/telegram.md).

Ở nơi không truy cập được Telegram: dùng bot nhóm của WeCom, Feishu hoặc DingTalk, hoặc đặt `TG_API_HOST` trỏ tới
reverse proxy của riêng bạn. Xem [other-channels.md](skills/setup-alert-channel/references/other-channels.md).

## Cảnh báo hoạt động thế nào

Những quy tắc giữ cho kênh cảnh báo còn đáng đọc:

- **Chỉ báo khi có thay đổi.** Báo lúc sự cố bắt đầu và lúc kết thúc, kèm thời gian kéo dài. Không báo mỗi phút.
- **Xác nhận rồi mới báo.** Ba lần đọc xấu liên tiếp trên máy chủ, hai lần probe hỏng từ Worker. Một phút trục
  trặc không làm phiền ai.
- **Không chập chờn.** Cảnh báo đang mở chỉ đóng khi giá trị xuống dưới ngưỡng một khoảng.
- **Mỗi lần chạy một tin**, gom mọi thay đổi. Sự cố mới có âm báo; phục hồi và báo cáo thì không.
- **Sự cố nghiêm trọng được nhắc lại** mỗi 4 giờ khi còn mở; cảnh báo nhẹ chỉ nói một lần.
- **Tin gửi hỏng được giữ lại** và gửi cùng lần chạy sau.
- **Im lặng nhưng vẫn theo dõi**: `silence 2h` khi bảo trì; hết thời gian sẽ báo những gì vẫn còn hỏng.
- **Báo cáo hằng ngày** kiêm bằng chứng còn sống: không thấy báo cáo nghĩa là chính monitor đang hỏng.
- **Có người canh người canh gác**: máy chủ gửi heartbeat cho Worker; `/health` của Worker được máy chủ kiểm tra.

## Những gì được cài

Trên mỗi máy chủ (gỡ bằng một lệnh, có ghi trong skill):

```
/opt/infrawatch/infrawatch.py                       monitor
/etc/infrawatch/config.ini                          theo dõi cái gì; không chứa bí mật
/etc/infrawatch/notify.env                          token; chỉ root đọc, quyền 600
/etc/systemd/system/infrawatch.{service,timer}      chạy mỗi phút
/var/lib/infrawatch/state.json                      cảnh báo đang mở, baseline
```

Trong tài khoản Cloudflare của bạn: một Worker (`infra-alerts`) và một database D1, nằm trong gói miễn phí với
quy mô này ([con số cụ thể](skills/monitor-cloudflare/references/limits.md)).

Không có gì gửi về cho chúng tôi hay bên thứ ba. Lưu lượng đi ra chỉ tới các dịch vụ chat bạn cấu hình và giữa máy
chủ của bạn với Worker của bạn.

## Kênh thông báo

| Kênh | Agent trên máy chủ | Worker | Ghi chú |
|---|:-:|:-:|---|
| Telegram | ✅ | ✅ | nhóm, kênh, chủ đề; `TG_API_HOST` nếu cần proxy |
| WeCom / 企业微信 | ✅ | ✅ | |
| Feishu / Lark | ✅ | ✅ | hỗ trợ chữ ký |
| DingTalk / 钉钉 | ✅ | ✅ | chữ ký hoặc từ khoá `infra` |
| Webhook tuỳ ý | ✅ | ✅ | JSON `{title, body, level, source}` |
| Bark, ntfy, ServerChan (Server酱), PushPlus | ✅ | – | |

## Phương án và hiệu quả ở allshorts.net

Tóm tắt từ [docs/approach.md](docs/approach.md). Chúng tôi chỉ chia sẻ cách làm, không mô tả hệ thống:

- **Bảo mật.** Token nằm riêng trong một file chỉ root đọc được, không bao giờ ở trong repo hay file cấu hình.
  Monitor không mở cổng nào, chỉ gửi đi. Lần chạy đầu nó ghi lại trạng thái bình thường (ai đăng nhập, file cấp
  quyền, cổng đang mở, container), sau đó báo một lần về bất cứ thứ gì mới. Nó chỉ báo, con người quyết định. Một
  watcher ở bên ngoài nhận heartbeat, vì máy đã sập hoặc bị chiếm thì không tự báo được.
- **Chặn crawl.** Luật ở biên (bot challenge, giới hạn theo địa chỉ) chặn được crawler thô, nhưng crawler kiên
  nhẫn thì từng request đều trông như người thật. Vì vậy một job định kỳ nhìn lưu lượng như một tổng thể qua API
  analytics của CDN, báo một lần kèm luật firewall đề xuất, và con người là người áp luật. Crawler của công cụ tìm
  kiếm được loại trừ từ gốc. Giới hạn bền nhất được đặt trong chính ứng dụng, nơi biết tài khoản và thế nào là
  một lượt xem bình thường.
- **Cảnh báo.** Một chat duy nhất. Chỉ báo thay đổi, sau khi đã xác nhận nhiều lần liên tiếp, gom thành một tin.
  Việc đang chạy không phải là lỗi: kiểm tra lại trước khi báo. Gửi thất bại thì giữ lại gửi sau. Báo cáo định kỳ
  cũng là bằng chứng monitor còn sống.
- **Hiệu quả.** Một tin trong chat nghĩa là có việc cần người làm. Những kiểu lưu lượng mà luật theo từng request
  không thấy đã lộ ra khi nhìn tổng thể. Không ai phải nhớ đi kiểm tra: hệ thống tự nói khi cần, kể cả khi chính
  nó có vấn đề.

Chúng tôi không công bố số lượng cảnh báo, ngưỡng phát hiện hay chi tiết sự cố.

## Tình trạng và phạm vi đã kiểm thử

Phiên bản 0.1.0. Nói thẳng về một bản phát hành đầu:

- `infrawatch.py`: 44 test, chạy trên Python 3.8 và 3.11. Các bộ thu thập được test với output ghi lại của `df`,
  `/proc`, `systemctl`, `docker`, `nvidia-smi`, `ss`, `journalctl`; logic cảnh báo và các lệnh được test đầu cuối
  với một máy chủ giả đóng vai Telegram.
- Worker: 14 test trên Node với SQL engine thật thay cho D1, và một lần chạy trong runtime của Cloudflare
  (`wrangler dev`) qua kịch bản sập, phục hồi, heartbeat và các endpoint.
- **Chưa làm:** phiên bản agent này chưa được tác giả chạy trên máy chủ Linux production, và các kênh ngoài
  Telegram được viết theo tài liệu của từng dịch vụ nhưng chưa gửi qua dịch vụ thật. Vì vậy skill cài đặt bắt
  Claude chạy `check` và `test` trên máy của bạn rồi cùng bạn đọc kết quả trước khi bật. Nếu một phép kiểm tra
  đọc sai hệ thống của bạn, hãy mở issue kèm output của `check`.

```bash
python -m unittest discover -s tests
node --test tests/worker.test.mjs
```

## Chưa có

- Lịch sử, biểu đồ, trang trạng thái. Công cụ này báo vào chat, không lưu metric. Dùng kèm Beszel hoặc Netdata.
- Cảnh báo từ analytics của Cloudflare (tỉ lệ 5xx, lỗi Worker, hạn mức).
- Máy chủ Windows và macOS.
- Tự hành động (khởi động lại dịch vụ, kill tiến trình). Nó chỉ báo; con người hoặc skill triage mới hành động.

## Giấy phép

MIT. Xem [LICENSE](LICENSE).
