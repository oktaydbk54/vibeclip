# shorts-mcp · Pro Editör Katmanı — FİNAL SENTEZ PLANI

## 1. Yönetici Özeti

Kazanan iskelet **Plan C (aşamalı-hibrit)**: her fazı tek başına ship edilebilir tutması, borçları (cut hash-cache, GC, subtitle monkeypatch) açık fazlara gömmesi ve `/api/tool` whitelist omurgasını ilk günden kurması, "küçük fazlar + düşük risk" ölçütünde diğer ikisini açık farkla geçiyor. Ürün sıralaması ise **Plan A'dan** alındı: transkript editörü en yüksek algı sıçramasını en hazır backend'le (`words_for` invariant'ı + `anchor_text`'li `remove_section`) verir; bu yüzden timeline'dan ÖNCE gelir — B'nin "önce zaman temeli" sırası teknik olarak temiz ama ilk iki sprintte kullanıcıya görünür değer üretmiyor. **Plan B'den** üç kritik fikir aşılandı: (1) timeline'ın x-ekseni = player zamanı kararı ve "timeline = project.json'ın üçüncü görünümü, asla gerçeğin kaynağı değil" ilkesi, (2) `fps_rational` + `requestVideoFrameCallback` titizliğiyle zaman temeli, (3) plan-on-timeline **ghost-diff** — vibe→pro köprüsünün kendisi ve rakipsiz ayrıştırıcı. A'nın `timemap` modülü (kaynak↔çıktı çift yönlü harita) hem "kesilenleri göster"in hem NLE export'un tek çekirdeği olarak korundu. Vibe katmanı hiçbir fazda değişmez: tüm pro UI etkileşimleri gpt-4o-mini'yi bypass edip aynı `REGISTRY` impl'lerinden geçer, chat'e eklenen tool sayısı toplamda ≤4 tutulur.

**Her fazda korunan değişmezler:** stage-replay + hash-artifact disiplini (UI asla video dosyasına dokunmaz, yalnız params patch'ler); `words_for` invariant'ı (transkript zamanları = player zamanları, her fazın testinde açıkça doğrulanır); FastAPI + vanilla JS, build step yok; her mutasyon `session.snapshot()` ile history'ye girer.

---

## 2. Faz Listesi

### Faz 1 — Hızlı Kazanımlar + Bloke Eden Borçlar (Efor: **S**, ~3-4 gün)

**Hedef:** En ucuz "oyuncak değil" sinyali (SRT/VTT) + sonraki tüm fazların omurga endpoint'i + iki teknik borcun kapatılması.

**Kapsam:**
- `pipeline/captions.py` (yeni): `subtitle.py`'deki segmentasyon `build_caption_segments()` olarak çıkarılır (burn-in ve sidecar AYNI segmenter — ekran ≡ dosya garantisi); `to_srt()` / `to_vtt()`. Kaynak: `session.words_for(clip)` → cache-hit, ek transkripsiyon yok.
- `chat/tools.py`: `export_captions(clip_id, format)` tool'u (chat'ten de çağrılabilir).
- `chat/app.py`: `GET /api/captions/{clip_id}.{srt|vtt}` + **`POST /api/tool` omurga endpoint'i** — `{name, args}` alır, whitelist'li REGISTRY impl'ini doğrudan çağırır (Faz 1 whitelist: `export_captions`, `undo`); her çağrı history'ye `source:"ui"` etiketiyle düşer (Faz 5'in edit-log'unu şimdiden besler).
- **Cut hash fix:** `pipeline/cut.py:64` sabit `outputs/{i:02d}_{ad}.mp4` adı → `_out()` hash adı; `session.py:271-280` bypass kaldırılır. Eski sabit-adlı dosya varsa hash adına `os.link` ile taşınır (geriye uyumlu). Sabit-adlı çıktı davranışı Faz 6'daki "mp4 indir" anına taşınır.
- `chat/gc.py` (yeni): mark-and-sweep — tüm history snapshot'ları + `current` + pending_plan preview'ları referans seti; referanssız ve >7 gün artifact'ler silinir. Varsayılan **dry-run**, silme `--force`/onaylı; `POST /api/gc`.

**Veri modeli:** project.json'a alan girmez. History girdilerine opsiyonel `source` kwarg'ı (eski girdiler alansız okunur).

**Riskler:** GC yanlış pozitifi → dry-run varsayılan; cut-hash geçişinde eski session'ların ilk replay'i bir kez yeniden keser (kabul).

**Doğrulama:** `to_srt` golden-file testleri (Türkçe karakter, 1 kelimelik segment, 00:00 sınırı); SRT'yi VLC/YouTube'da burn-in ile kare kare karşılaştır; cut-fix sonrası `duplicate_clip` + iki varyantta farklı edit → tek cut artifact'i paylaşılıyor (log'da re-encode yok); GC dry-run çıktısında hiçbir `current` dosyası yok assert'i.

---

### Faz 2 — Transkript Editörü v1: Text-Based Editing (Efor: **M**, ~1-1.5 hafta)

**Hedef:** Kelimeye tıkla → seek; aralık seç → sil (video kesilir); filler önizleme. Paradigmanın kendisi en düşük maliyetle ship olur — pro algı sıçraması burada.

**Kapsam:**
- `chat/app.py`: `GET /api/transcript/{clip_id}` → `words_for()` çıktısı `{words:[{i,start,end,word,is_filler}], segments, fps}` (klip-lokal zaman; `is_filler` sunucuda `FILLER_WORDS` ile işaretlenir, UI liste kopyalamaz).
- `/api/tool` whitelist'ine `remove_section`, `remove_fillers` eklenir.
- `remove_fillers` impl'ine `preview=True` parametresi (Plan B): mutasyonsuz aday listesi döner.
- `chat/static/transcript.js` (yeni) + `index.html` orta panele **Metin | Video** yan yana görünüm: kelimeler `<span data-i data-start data-end>`; tık → seek; `timeupdate` ile aktif kelime karaoke-highlight (binary search). Seçim (span-indeks tabanlı, deterministik) → yüzen "Kes (N kelime)" çubuğu → `POST /api/tool remove_section` — **start/end + anchor_text birlikte gönderilir**: saniyeler belirsizliği çözer, anchor upstream-retime sigortası kalır (mevcut `_locate_anchor` davranışı, değişiklik yok). Kesim sınırları her zaman kelime `start/end`'ine snap'lenir, yarım kelime asla gitmez.
- Filler'lar soluk turuncu; "Tümünü temizle (N)" çipi → `remove_fillers`.
- Kesim sonrası transkript yeniden fetch; bir tur "üstü çizili hayalet" gösterimi, sonra reconcile.
- Render sırasında panel disable + spinner (senkron blocking bu fazda kabul; Faz 3 çözer).

**Veri modeli:** değişiklik yok (`remove_section` zaten trim `ranges`'a yazıyor).

**Riskler:** `>%50 silme reddi` UI'da anlamlı hata kartına çevrilmeli; Whisper kelime sınırı ±50-100 ms kayması → Faz 4'teki frame-nudge ile kapanacak, şimdilik ±0.02s pad; 2-3k span DOM yükü 60-90 sn klipte sorun değil, segment-lazy-render hazır dursun.

**Doğrulama:** Invariant testi — rastgele 10 kelimeye tıkla, player o kelimeyi söylüyor (±0.1 sn); orta cümleyi sil → kalan kelimelerin tıkla-seek'i hâlâ doğru; sil → undo → tekrar sil = cache-hit; aynı cümlenin iki kez geçtiği klipte ikinci geçişi sil → doğru aralık gitti; UI undo = chat undo aynı snapshot.

---

### Faz 3 — Async Render Çekirdeği: Kuyruk + SSE + İptal + Subtitle Fix (Efor: **M**, ~1-1.5 hafta)

**Hedef:** Hiçbir HTTP isteği render beklemez; canlı ilerleme + iptal. Faz 4-6'daki tüm etkileşimli pro davranışın ön koşulu; tek başına da ship: chat'in kör spinner'ı ölür.

**Kapsam:**
- `chat/jobs.py` (yeni): **tek worker-thread'li** in-process kuyruk (celery/multi-process YOK — tek kullanıcı, global SESSION; kuyruk mutasyonları serileştirir, mevcut global-state varsayımları değişmeden güvenli kalır). `Job{id, kind, clip_id, status, progress, message, cancel_event, result}`.
- `pipeline/ffutil.py` (yeni) ortak `run_ffmpeg(cmd, on_progress, cancel_event)`: tüm uzun encode'lara `-progress pipe:1 -nostats`; `out_time_us / beklenen_süre` → yüzde. Pipeline imzaları değişmez (callback thread-local job context'ten).
- **İptal = atomiklik:** ffmpeg her zaman `tmp` ada yazar + `os.replace` (hash-cache'in "dosya varsa hazırdır" varsayımı korunur); iptalde job-başı snapshot'a rollback (yalnız worker thread'inde, `apply_plan`'ın `suppress_snapshots`'ından bağımsız job-seviyesi snapshot).
- `chat/app.py`: `POST /api/chat` ve `POST /api/tool` → anında `{job_id}`; `?sync=1` ile eski senkron mod (CLI/test); `GET /api/events` SSE (`StreamingResponse`, 15 sn keep-alive; vanilla `EventSource`, WS gereksiz) — `job_progress|job_done|job_error|state_changed|last_notes` event'leri; `POST /api/jobs/{id}/cancel`. SESSION çevresine `RLock`.
- **Subtitle monkeypatch kaldırılır:** `pipeline/subtitle.py`'ye `@dataclass SubStyle`; `burn_subtitles(..., style)`; `session.py:372-399` global set/restore bloğu silinir. Alan adları aynı → hash'ler değişmez.
- UI: appbar'da job çipi (etiket + % + iptal); render süren klibin panelleri "kuyrukta" rozetiyle soluk; transkriptteki optimistic strike-through `job_done`'da reconcile.

**Veri modeli:** değişiklik yok (job state runtime-only — tek kullanıcı, persist gereksiz).

**Riskler:** SSE + senkron agent turu → agent turu da executor'da, tool-içi renderlar aynı job'ın alt-progress'leri; SSE kopması → `EventSource` auto-reconnect + `GET /api/jobs/{id}` polling fallback.

**Doğrulama:** `apply_style` → SSE'de 6 aşamanın yüzdeleri akıyor, UI bu sırada canlı; %50'de iptal → project.json job-öncesiyle birebir aynı (diff testi), `tmp` temiz, aynı işlem tekrar temiz çalışıyor; subtitle fix regresyonu: aynı params → aynı hash dosyası + pixel-diff örneklemi; chat plan-önerisi/onayı uçtan uca değişmedi.

---

### Faz 4 — Zaman Temeli + Salt-Okunur Timeline + Klavye Seti (Efor: **M**, ~1.5-2 hafta)

**Hedef:** Pro'nun 5 dakikalık güven testi: gerçek timecode, ±frame, J-K-L, I/O ve project.json'dan türetilen çok-şeritli timeline. Hiçbir render tetiklemez.

**Kapsam:**
- `pipeline/timebase.py` (yeni, Plan B titizliği): `Timebase(num, den)` — ffprobe `r_frame_rate` rational ("30000/1001" NTSC-doğru); `snap_s`, `to_frames`, `tc()`, `parse_tc()`. **Saklama formatı float saniye kalır**; snap yalnız edit sınırlarında. Timebase **çıktı** fps'inden okunur (cut çıktıları zaten CFR); kaynak fps yalnız Faz 6 conform'da.
- `chat/timemap.py` (yeni, Plan A çekirdeği): cut → jumpcut keep-segmentleri → trim ranges kompozisyonundan **kaynak↔çıktı çift yönlü harita**; `jumpcut.py`'ye `return_segments=True` (keep-aralıkları artifact yanına hash-adlı `*.segments.json`). Faz 6 NLE export'un ve "Kesimler" şeridinin tek kaynağı.
- `chat/timeline_view.py` (yeni): `serialize(clip)` → track JSON: `cuts` (timemap tikleri), `zoom`, `broll/overlay/fx`, `sfx`, `subtitles` (segmenter'dan), `music/ambience/fade`, `markers`. `GET /api/timeline/{clip_id}` — saf state türevi, render yok. **X-ekseni = player zamanı** (Plan B kararı — `words_for` ve tüm post-timing event'lerle aynı uzay; kaynak-uzayı görselleştirmesi yapılmaz).
- **Veri modeli (tek additive alan):** `clips[].markers = [{id, t, label, color, origin:"ai"|"user"}]`; AI marker'ları `structure.py` moment index'inden timemap ile player-uzayına çevrilir. Tool'lar: `add_marker` / `remove_marker` (chat'e de girer — +2 spec).
- `chat/static/timeline.js` (yeni): ruler tek `<canvas>` (dPR-farkındalıklı); event kutuları **DOM div** (Faz 5'te pointer-event hedefi olacaklar — Plan B mimarisi); playhead `translateX` + rAF; yatay zoom `ctrl+wheel`, pan `wheel`; şerit-başına virtualization. Tık → seek + transkriptte o kelimeye scroll; transkript seçimi timeline'da highlight (**üç yüzey senkronu**: timeline ↔ player ↔ transkript).
- **Klavye + player** (`app.js`): sahte `setInterval` sayacı (`app.js:14-22`) SİLİNİR → `requestVideoFrameCallback(mediaTime)`'dan gerçek `HH:MM:SS:FF` (feature-detect, fallback `seeked`); `Space`, `K` pause, `L` 1x→2x→4x, `J` geri-scrub (HTML5 negatif rate yok → 100 ms step emülasyonu, UI'da bilinen sınır olarak etiketli), `←/→` ±1 frame (`(frame±1+0.5)/fps` yarım-frame ofseti), `Shift` ±10, `I/O` in/out (client-state + timeline'da sarı bant), `M` marker, `G`/tıklanabilir timecode → `parse_tc` → seek, `Cmd+Z` undo.
- `I/O` + "Kes" → `/api/tool remove_section {start,end}` (anchor_text sunucuda `words_for`'dan otomatik doldurulur) — timeline daha sürüklenemezken bile **edit yapabilen** araç.
- Scrub keskinliği: encode satırına `-g <fps>` (1 sn GOP; ~%10 boyut, kabul). Tam proxy hattı YAPILMAZ (bkz. §3).
- Chat paritesi: `nudge_edit(clip_id, target, edge, frames)` mini-tool — "kesimi 4 frame geri al" aynı snap katmanından (+1 spec).

**Riskler:** VFR kaynak → ingest'te ffprobe ile tespit, UI'da "yaklaşık frame" uyarısı, Faz 6'da sert uyarıya döner; J emülasyonu gerçek reverse değil (dokümante); canvas kapsam şişmesi → v1'de hit-test yok, sadece seek.

**Doğrulama:** `→` 10 kez = timecode'da tam 10 frame, gösterilen kare her adımda değişti (rvfc doğrulaması); `tc(parse_tc(x))==x` round-trip + 29.97 non-drop unit testleri; timemap property-testi: kept bölgelerde `to_output ∘ to_source = id`, `out` toplamı = ffprobe süresi ±1 frame; bilinen zamana `add_sound_effect` → timeline noktası + duyulan an + timecode üçü eşit; chat'ten edit sonrası şeritler `state_changed` ile güncelleniyor.

---

### Faz 5 — Etkileşimli Timeline + Transkript v2 + Picture-Lock & Edit Log (Efor: **L**, ~2 hafta)

**Hedef:** Timeline editöre döner (drag/resize/inspector); transkriptte "kesilenleri göster + geri al"; vibe↔pro güven köprüsü (lock + günlük + otonomi eşiği).

**Kapsam:**
- **Veri modeli:** tüm event/window öğelerine kalıcı `id` (kısa uuid) — zoom `windows` `[{id,start,end,strength}]`'e normalize (okuyucu eski `[[s,e,z]]` formunu migrate eder, `_retime_params` id'leri korur); `clips[].locked: bool`; proje `autonomy: "ask_all"|"auto_minor"`; history girdileri `{tool, args, source, reason?, ts}` ile zenginleşir + snapshot `clips` deepcopy'leri `history/{ts}.json` sidecar'a taşınır (project.json şişme fix'i, limit 20→100; eski projeler tek seferlik migrate + yedek).
- **Drag:** kutu gövdesi = taşı, kenarlar = resize; client-side frame-snap + komşu event/kelime sınırına magnet; `pointerup` → 250ms debounce → `POST /api/tool set_stage_params {clip_id, stage, params}` (yeni generic tool: `set_stages`'in ince sarmalayıcısı, **chat TOOL_SPECS'e verilmez** — 4o-mini serbest param JSON'uyla baş başa bırakılmaz). Optimistik yerleşim + "render…" rozeti; hata → snap-back + toast. Replay süren klipte şeritler disabled.
- Timing-edge drag (kesim bloğu kenarı) bu fazda açılır — frame-snap artık var. `cuts` tiki sağ-tık → "Bu kesimi geri al": `jumpcut` params'a `protected_ranges` muafiyeti (S-boy ekleme, birim test şart).
- **Inspector** sekmesi (sağ panel "Copilot | Inspector"): seçili event alanları (zoom strength slider, sfx kind/volume, overlay opacity, subtitle stili) → tek `set_stage_params`; sticker konumu `safearea.clamp_center`'dan geçer.
- **Transkript v2:** `GET /api/transcript?full=1` → timemap'le her kelimeye `state: kept|cut_silence|cut_filler|cut_manual`; "kesilenleri göster" toggle → üstü çizili soluk gri, tık → `restore_section(clip_id, start, end)` (yeni tool: trim range'den çıkar / jumpcut `protected_ranges`'a ekler); transkript arama (`Cmd+F`, client-side); seçim aksiyon çubuğuna ±frame nudge + **Zoom/Vurgu/SFX** kısayolları (mevcut tool'lara map).
- **Picture-lock:** `lock_clip/unlock_clip`; `set_stages` guard'ı — locked klipte `TIMING_STAGES` yazımı açıklayıcı hatayla reddedilir (UI drag'i de plan adımı da aynı kapıdan döner); görsel/ses aşamaları serbest (gerçek lock semantiği). Timeline'da kilit ikonu.
- **Edit log + otonomi:** sağ panelde "Düzenleme Günlüğü" — her satır `tool + args özeti + AI/sen/plan rozeti + neden`, tık → `/api/restore/{i}`. `agent.py`'de otonomi kapısı: adımlar statik tabloyla minor (remove_fillers, set_loudness, set_fade) / structural (cut/trim/jumpcut/remove_section) sınıflanır; `auto_minor`'da salt-minor planlar onaysız `apply_plan` (tek snapshot = tek undo), structural her zaman plan kartına. Şüphede "structural".
- Chat paritesi: `move_event` / `delete_event` (+2 spec — toplam chat ekleme tavanı burada dolar).

**Riskler:** I/O-sil timing değiştirir → `_retime_params`'ın manuel-event temizliği `last_notes` SSE-toast'la yüzeye + undo ile geri; `protected_ranges` select ifadesi karmaşası → birim test; sidecar history migrasyonu → yedekli, tek seferlik.

**Doğrulama:** Zoom kutusunu 2 sn çek → tek tail re-encode (log), undo → eski hash'e anlık dönüş (cache-hit); I/O ile 4 sn sil → `auto` zoom yeniden planlandı, elle sfx temizlendi + toast geldi; `cut_silences` sonrası kesilen kelimeler işaretli, birini geri getir → o duraklama videoda geri, diğerleri durdu; locked klipte `remove_section` → hata + state değişmedi; `auto_minor`'da filler planı onaysız uygulandı, cut içeren plan kartta bekledi; 30 ardışık edit sonrası project.json boyutu sabit.

---

### Faz 6 — Pro Handoff: NLE Export + QC Kartı + Plan-on-Timeline (Efor: **M**, ~1.5 hafta)

**Hedef:** "AI kaba kurguyu yapar, editör NLE'de bitirir" sözleşmesi: az ama DOĞRU export (Descript'in kırdığı yer), ölçülmüş teslim kartı ve plan önerilerinin timeline'da ghost-diff'i.

**Kapsam:**
- `pipeline/nle_export.py` (yeni): Faz 4 `timemap.kept_source_ranges()` → kaynak-zamanlı kesim listesi. **Yalnız kesimler + marker'lar taşınır** — zoom/overlay/fx/altyazı stili bilinçli taşınmaz (lossy çeviri = kırık export algısı); altyazı sidecar SRT/VTT olarak pakete girer; `EXPORT_NOTES.txt` neyin taşınıp taşınmadığını yazar.
  - **OTIO** (`opentimelineio` pip) first-class: tek video track, media reference = kaynak dosyanın **mutlak yolu** + `fps_rational` ile rational-time conform (Descript şikayetlerinin ana kaynağı float-fps'ti); marker'lar OTIO markers.
  - **FCPXML + Premiere XML** OTIO adapter'larıyla; **CMX3600 EDL** elle (~80 satır). Hedef: Resolve + Premiere import testi geçen TEK şema sürümü, fazlası sonra.
  - Otomatik doğrulama: üretilen OTIO `read_from_file` ile geri okunup kesim listesiyle diff'lenir; VFR kaynakta sert uyarı ("önce sabit-fps transcode").
- Tool'lar: `export_timeline(clip_id, fmt)`, `export_delivery(clip_id, aspects, platform)` (reframe varyantları `build_reframe_vf` farklı canvas'larla + ölçümlü loudnorm; async job).
- `GET /api/qc/{clip_id}`: mevcut iki-geçişli loudnorm **ölçüm geçişi** (`effects.py:84-132`, render yok) + ebur128 true-peak + ffprobe → `{lufs, true_peak, duration, res, fps, warnings[]}`.
- **Teslim paneli** (UI): format matrisi (mp4 9:16/1:1/16:9 · srt · vtt · otio · fcpxml · edl), QC kartı ("ölçülen −14.2 LUFS / −1.1 dBTP ✓ · 58.2/60 sn ✓"), player üstü **safe-zone overlay toggle** (saf CSS/SVG, `safearea.py` sabitlerinden — render maliyeti sıfır). `clips[].exports = [{type, path, qc, ts}]` log'u.
- **Plan-on-timeline (Plan B'nin kreşendosu):** `planner.propose` sonrası kod-tarafı post-process — her adıma deterministik `affects: [{clip_id, stage, ranges|events}]` (LLM'e dokunulmaz); `_render_plan_preview`'ın sessiz `return None`'ı adım-bazlı `{step, error}` raporuna çevrilir. UI: `pending_plan` varken timeline diff modu — ekler kesikli-yeşil ghost, silinecekler kesikli-kırmızı, değişenler amber; ghost'a tık = B-preview videosunda o ana seek (mevcut A/B player aynen); ONAYLA/VAZGEÇ → mevcut `apply_plan/discard_plan`, onayda render'lar cache-hit (speculative preview zaten diske yazdı). `affects` türetilemeyen adım plan kartında kalır (zarif degrade).

**Veri modeli:** `clips[].exports` log'u; başka alan yok.

**Riskler:** FCPXML fps-conform → test matrisi 29.97/25/30/60; `join_clips` kompozisyonları eager-render düz dosya → comp export v1'de yalnız üye kliplerin ayrı OTIO'su + sıralı EDL (lazy-kompozisyon bilinçli plan dışı); jumpcut'ın çok-parçalı listesi uzun EDL üretir (sorun değil).

**Doğrulama:** **En kritik kabul testi:** FCPXML/OTIO'yu Resolve'a (ücretsiz) import → kaynak otomatik bağlanıyor, 3+ rastgele kesim noktası in-app player'la frame-frame eşit; EDL Premiere'e import; QC LUFS değeri bağımsız `ffmpeg -af ebur128` ile ±0.3 LU; 61 sn klip + Shorts hedefi → süre uyarısı; ghost-diff: "daha dinamik yap" → ghost'lar doğru zamanlarda, ONAYLA → tüm adımlar cache-hit anlık.

---

## 3. Bilinçli Olarak YAPILMAYACAKLAR

| Karar | Neden |
|---|---|
| **Multitrack / multicam** | "clip = (source_ref, in, out)" yeniden mimarisi + ses mixer'ı = en yüksek maliyet; mevcut tek-kaynak shorts kitlesi için marjinal. Podcast/röportaj segment kararı verilirse 6 faz sonrası İLK iş; ara adım `join_clips`'in lazy-kompozisyona çevrilmesi olur. |
| **Tam scene/layout switcher** | Tek-kaynakta sınırlı anlamlı, multicam'e bağımlı. Ucuz versiyonu (marker/bölüm şeridi) Faz 4'te zaten alınıyor. |
| **Bad-take seçimi** | P1 ama LLM kümeleme gürültü riski yüksek ve UI'ı yeni iş; timemap + transkript v2 + `restore_section` altyapısı (Faz 4-5) olgunlaşmadan girilmemeli. 6 faz sonrası ilk adaylardan; `pick_variant`/`archived` iskeleti hazır bekliyor. |
| **Review linki + timecode'lu yorum + "yorumdan plana"** | Gerçek ayrıştırıcı ama bu planın temellerine (async, timemap, marker, `affects`) bağımlı; Faz 6'nın `affects` altyapısı doğal girişi olur — ayrı P1 planı olarak sonra. |
| **Tam proxy hattı (480p ayrı dosya)** | Faz 4'teki `-g <fps>` kısa-GOP ucuz çözümü ≤90 sn kliplerde yeter; scrub yine de takılırsa proxy bağımsız S-M iş olarak eklenir — şimdiden mimari taahhüt gereksiz. |
| **Karaoke kelime-PNG → sprite-sheet/libass yeniden yazımı** | Faz 3 SubStyle fix'i acil kısmı (monkeypatch) çözer; ölçek tavanı ancak >90 sn klipler hedeflenirse gündeme gelir. |
| **Çoklu kullanıcı / çoklu proje** | İş modeli kararı; tek-worker job mimarisi bilerek buna göre boyutlandırıldı — erken genelleme tüm fazları şişirir. |
| **Zoom/fx/altyazı stilinin NLE'ye taşınması** | Lossy çeviri = "kırık export" algısı (Descript dersi). Kesim + marker + sidecar SRT = az ama doğru. |
| **Chat tool spec şişmesi** | Toplam +4-5 spec tavanı (`export_captions`, `add/remove_marker`, `nudge_edit`, `move/delete_event`); `set_stage_params` chat'e ASLA verilmez — 4o-mini'nin güvenilirlik zarfı korunur. |

---

## 4. Açık Sorular (kullanıcıya)

1. **Hedef NLE önceliği:** İlk import-test matrisinde hangisi birinci sınıf — DaVinci Resolve (ücretsiz, test kolay) mı, Premiere mi, Final Cut mu? Faz 6'nın şema-sürüm kararını belirler.
2. **Podcast/röportaj pazarına açılma niyeti var mı?** Varsa multicam erteleme kararı gözden geçirilmeli ve Faz 5'teki event-id modeli `source_ref` alanını şimdiden taşıyacak şekilde genişletilebilir (ucuz sigorta).
3. **Klip uzunluğu tavanı:** Hedef hep ≤90 sn mi? >90 sn (uzun YouTube kesitleri) hedefleniyorsa proxy hattı ve karaoke sprite-sheet işi plana geri girer.
4. **Otonomi varsayılanı:** Yeni projeler `ask_all` ile mi `auto_minor` ile mi açılsın? (Pro güveni için muhafazakâr `ask_all` öneriyorum; tek satırlık karar.)
5. **J tuşu emülasyonu kabul mü?** HTML5 gerçek reverse-play desteklemiyor; step-emülasyonlu J "bilinen sınır" olarak yeterli mi, yoksa reverse-scrub için kısa-GOP proxy öne mi çekilsin?
6. **GC otomasyonu:** Manuel `POST /api/gc` (dry-run varsayılan) yeterli mi, yoksa job kuyruğu boşken otomatik tetiklensin mi?
7. **Denoise stage'i (fırsat işi):** `afftdn`/`arnndn` tek yeni stage, hash-cache bedava — Faz 5 veya 6 sprintine S-boy fırsat işi olarak eklensin mi, yoksa tamamen sonraya mı?

---

### 4.1 Boran'ın Yanıtları (2026-06-10) — Faz 6 bunlarla kilidi açıldı

1. **NLE önceliği → DaVinci Resolve birinci sınıf.** Import-test matrisi önce Resolve'da sağlamlaştırılır (ücretsiz, FCPXML+EDL+OTIO okur, en hızlı doğrulama); Premiere/FCP ikinci dalga. Şema-sürüm kararı Resolve uyumuna göre.
2. **Multicam/Podcast → EVET, `source_ref` şimdiden eklenecek.** Faz 5 indeks-tabanlı event modeline `source_ref` alanı (ucuz sigorta) Faz 6 başında taşınır; multicam RENDER yine ertelenir ama veri modeli hazır olur.
3. **Klip uzunluğu → SABİT TAVAN YOK; LLM video yapısını inceleyip karar verir.** ≤90 sn varsayımı kaldırıldı. Klip uzunluğu değişken (LLM-driven) → uzun klipler mümkün → **proxy render hattı + karaoke sprite-sheet** uzun-klip yolu için Faz 6 planına geri alındı (yalnız uzun klipte devreye girer, kısa klip mevcut hattı kullanır).
4. **Otonomi varsayılanı → `ask_all` (muhafazakâr).** Yeni projeler her planı onaya sunarak açılır; kullanıcı `set_autonomy auto_minor` ile geçer. (Faz 5'te zaten bu varsayılan.)
5. **J tuşu → step-emülasyon yeterli.** Gerçek reverse-scrub/kısa-GOP proxy öne çekilmez; J küçük geri adımlar olarak "bilinen sınır".
6. **GC → OTOMATİK tetikle.** Manuel `POST /api/gc` korunur ama iş kuyruğu boşken otomatik GC çalışır (eski + referanssız ara render'lar). Faz 6'da JobManager idle-hook'una bağlanır; dry-run değil gerçek süpürme, current asla silinmez güvencesiyle.
7. **Denoise → Faz 6'ya ekle.** `afftdn`/`arnndn` tek yeni CANONICAL stage (hash-cache bedava), Faz 6 sprintine S-boy fırsat işi.