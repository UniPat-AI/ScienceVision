# DNS 排查交接 — babyvision-v2.unipat.ai (2026-06-26)

## 现象

浏览器报 `ERR_NAME_NOT_RESOLVED`。

## 客观事实（任何位置可复现）

```
$ dig +short @1.1.1.1 babyvision-v2.unipat.ai     # Cloudflare 公网 resolver
(empty)
$ dig +short @8.8.8.8 babyvision-v2.unipat.ai     # Google
(empty)
$ dig +short @223.5.5.5 babyvision-v2.unipat.ai   # AliDNS
(empty)

$ dig @ns09.domaincontrol.com babyvision-v2.unipat.ai
;; ->>HEADER<<- opcode: QUERY, status: NXDOMAIN, id: ...
;; flags: qr aa rd                                  ← aa = authoritative answer
```

`aa` flag 是关键：GoDaddy 权威 NS 自己说"我就是这个 zone 的权威，这条记录确实不存在"。
**不是传播延迟，是源头没有这条数据**。

## unipat.ai 的当前权威 NS

```
$ dig +short NS unipat.ai
ns09.domaincontrol.com.
ns10.domaincontrol.com.
```

域名注册商 + DNS 托管都在 GoDaddy。任何对 babyvision-v2 A 记录的修改必须发生在 GoDaddy 控制台，且必须最终落到 zone 文件（dig 看 SOA 序列号 `2026062400` 说明 GoDaddy 今天的确编辑过 zone，但保存的内容里没有这条记录）。

## 验证 IT 配置的 3 种排查路径

请 IT 同事确认：

1. **改的是哪个域名**？现在权威是 unipat.ai。如果 IT 在 unipatai.com / unipat.com.cn 或其他变体上配，则不会生效。
2. **改的是哪个 DNS 平台**？unipat.ai 的解析必须落在 ns09/ns10.domaincontrol.com（GoDaddy DNS）。如果改在公司内部 DNS / Cloudflare / AWS Route53，必须先把 NS 委派过去。
3. **保存了吗**？GoDaddy 后台编辑后页面顶部会有黄色 "Save All Changes" 按钮，没点就不会落地。

## 目标记录

```
Type:  A
Host:  babyvision-v2          ← 不带 .unipat.ai 后缀，GoDaddy 自动补
Value: 47.84.143.121
TTL:   600  (or any value ≤ 3600)
```

## 待生效后的自动化路径

服务器侧已就绪：
- `deploy/Caddyfile.https.staged` 已替换好 hostname，`caddy validate` 已通过
- 后台 DNS poller 每 30s 探测，命中 47.84.143.121 后会触发 HTTPS 切换
