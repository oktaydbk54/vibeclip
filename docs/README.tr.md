# shorts-mcp

Uzun videoları (podcast / röportaj / eğitim) otomatik olarak viral kısa kliplere
böler: konuşmayı çözer, en iyi anları seçer, dikey 9:16'ya getirir ve kelime-senkron
altyazı yakar. Bir MCP server'dır — Claude veya MCP-uyumlu bir agent tool'ları çağırır.

## Pipeline

```
video → transcribe → analyze_structure → find_highlights → [auto_edit per clip]
        (whisper)     (sahne+enerji+konu)  (skorlu moment)
                                                            ↓
   jumpcut → tracked 9:16 reframe → (internal transitions) → punch zoom
           → karaoke captions → auto music+ambience (ducking) → SFX → fade
```

**Yapı-farkında seçim:** klipler düz metin taraması yerine gerçek konu/sahne
segmentlerinden, hook/flow/value alt-skorlarıyla seçilir.

## Tool'lar

| Tool | Ne yapar |
|---|---|
| `media_info(video_path)` | süre/çözünürlük/fps/codec |
| `transcribe(video_path)` | kelime-zamanlı transkript (cache'li) |
| `find_highlights(video_path, platform, count, max_duration)` | en iyi klip anlarını seçer (DeepSeek) |
| `cut_clip(video_path, start, end, title, precise)` | tek klip keser |
| `reframe_vertical(clip_path)` | 9:16 dikey, yüz-merkezli |
| `burn_subtitles(clip_path, words, clip_start)` | kelime-senkron altyazı yakar |
| `make_short(video_path, platform, count, max_duration, vertical, subtitles)` | **uçtan uca** |

`platform`: `youtube_shorts` \| `instagram_reels` \| `tiktok`

## Kurulum

```bash
cd shorts-mcp
cp .env.example .env        # DEEPSEEK_API_KEY doldur
uv sync
```

İlk `transcribe` çağrısında Whisper modeli iner. STT ayarları (`WHISPER_MODEL` vb.)
ve `DEEPSEEK_API_KEY` `.env` üzerinden gelir.

## Hızlı test

```bash
uv run server.py --selftest <video.mp4>        # ping + media_info
```

## Chat-editör (konuşarak düzenleme)

```bash
uv run python -m chat.cli <video.mp4>
```

Türkçe/İngilizce komut yaz: *"bu videodan 3 klip çıkar"*, *"2. klibe enerjik
müzik ekle"*, *"altyazıları büyüt"*, *"5. saniyeye zoom ekle"*, *"klibi göster"*,
*"geri al"*. Tek aşama değişikliği cache'li ara üründen replay edilir (~2-4sn).
Oturum `outputs/sessions/<ad>/project.json` dosyasında kalıcıdır.

### Web UI

```bash
uv run python -m chat.app <video.mp4> [port=8765]   # http://127.0.0.1:8765
```

### Vibe editing (V2.1–V3.0)

- **Stiller:** *"1. klibi hormozi tarzı yap"* — `hormozi`, `mrbeast`,
  `podcast_minimal`, `kinetic` (altyazı + tempo + zoom + müzik + sfx tek seferde).
  Kendi stilin: `assets/styles/<ad>.json`.
- **Cerrahi:** *"3 ile 5. saniye arasını çıkar"* (kelime-çapalı trim),
  *"eee'leri temizle"* (filler kelime silme), *"zoomları otomatik yerleştir"*,
  *"girişi 2sn erken başlat"* (set_cut).
- **Vibe planner:** *"daha punchy yap ama girişe dokunma"* → numaralı plan kartı
  → UYGULA/VAZGEÇ → tek "geri al" tüm planı geri alır.
- **B-roll:** *"konuya uygun b-roll ekle"* — Pexels stok video (ücretsiz
  `PEXELS_API_KEY` gerekir, pexels.com/api). Hook'un ilk 3sn'sine asla binmez.
- **Marka:** *"sağ üste logo koy"*, *"başlık kartı ekle"*.
- **Varyant/A-B:** *"1. klibin varyantını oluştur"* → kartta `var #1` rozeti,
  oynatıcıda A/B KARŞILAŞTIR; *"bunu seç"* → diğerleri arşivlenir.
- **Birleştirme:** *"1 ve 3'ü fade ile birleştir"* → tek kompilasyon videosu.
- **Geçmiş:** sağ panelde etiketli versiyon şeridi — tıkla, o ana geri dön.

- **Kendi asset'lerin (V4.1):** UI'dan "＋ YÜKLE" veya *"şu klasörü ekle"* —
  sistem otomatik anlar (vision etiketleme, renk, loudness). *"2. klip için
  asset önerisi yap"* → AI logonu/müziğini/b-roll'unu nereye koyacağını
  plan kartıyla önerir, eksik asset'i de söyler.
- **Craft FX (V4.2):** *"sinematik görünüm ver"* (`set_look`, %30-70 güç),
  *"film grain ekle"*, *"şu ana flash/vurgu koy"* (`add_emphasis`),
  *"meme reaction ekle"* (yeşil ekran chromakey), *"sticker koy"*.
- **Retention (V4.3):** *"daha akıcı yap"* (`auto_pace` — her 2-5sn'de
  titreşimli aralıklı bir değişiklik garantiler), *"sesi TikTok için ayarla"*
  (`set_loudness` — YT -14 / TikTok -11 LUFS, iki geçişli ölçümlü loudnorm).
  Sticker/watermark/altyazılar platform safe-area içinde kalır.
  `assets/sfx/`'e dosya at → yeni efekt türü (riser/impact/pop/boom/glitch hazır).
- **Zevk hafızası + stil kaydı (V4.4):** *"bundan sonra hep mrbeast altyazısı
  kullan"* → kalıcı tercih (planner her planda dikkate alır; *"tercihlerimi
  unut"* ile sıfırla). *"Bu ayarları 'kanalım' diye stil kaydet"* →
  `assets/styles/kanalim.json`, artık `apply_style` ile tek komut.
  *"yani/şey'leri de agresif temizle"* → her geçiş bağlamıyla LLM'e sorulur,
  sadece gerçek dolgular kesilir.

**UI — "KESİM Studio":** sol kütüphane (Klipler / Varlıklar / Geçmiş),
ortada telefon-çerçeveli önizleme + A/B karşılaştırma + pipeline node şeridi,
sağda YÖNETMEN AI copilot (plan kartları, stil ve ipucu çipleri).

> Müzik atıf gerektirir (CC BY): `assets/music/CREDITS.md`'deki satırı video
> açıklamasına ekle. SFX/ambiyans Mixkit — atıf gerekmez.

## MCP client'a ekleme (Claude Desktop / Claude Code)

```json
{
  "mcpServers": {
    "shorts": {
      "command": "uv",
      "args": ["--directory", "/path/to/vibeclip", "run", "server.py"]
    }
  }
}
```

Sonra: *"Bu videoyu YouTube Shorts için dikey + altyazılı 5 klibe böl"* →
agent `make_short(..., vertical=True, subtitles=True)` çağırır, klipler `outputs/`'a düşer.

## Sınırlar
- DeepSeek **videoyu görmez, transkripti okur** → konuşma ağırlıklı içerikte mükemmel,
  saf görsel/aksiyon highlight'ta sınırlı.
- Bu makinedeki ffmpeg libass'sız; altyazı Pillow+overlay ile basılıyor (bkz. PLAN.md).
