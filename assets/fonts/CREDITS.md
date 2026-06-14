# Bundled fonts — sources & licenses

Every font in this folder ships with VibeClip and is **freely licensed for
embedding and redistribution** (SIL Open Font License 1.1). They give the
meme / caption engine a portable, heavy display face that renders identically
on the dev Mac and on the Linux/Docker production server — without depending on
proprietary system fonts (real *Impact* / *Arial Black* are NOT free and are
never bundled).

`pipeline/subtitle.py:_resolve_font` automatically globs this directory, so any
`.ttf` dropped here also becomes a portable fallback for every caption style.

Keep this file in sync with the folder. All three were fetched from the
official Google Fonts repository (https://github.com/google/fonts), which
distributes them under the OFL.

----------------------------------------------------------------------
Anton-Regular.ttf           (logical key: `impact`)
  Family : Anton
  Source : https://github.com/google/fonts/tree/main/ofl/anton
  Design : Vernon Adams / Christian Robertson / Kimya Gandhi (Google Fonts)
  License: SIL Open Font License 1.1 — embedding & redistribution allowed
  Use    : The free Impact-alike. Tall, condensed, ultra-bold — the classic
           top/bottom meme caption and the "white-bar" meme headline face.
----------------------------------------------------------------------
ArchivoBlack-Regular.ttf    (logical key: `block`)
  Family : Archivo Black
  Source : https://github.com/google/fonts/tree/main/ofl/archivoblack
  Design : Omnibus-Type
  License: SIL Open Font License 1.1 — embedding & redistribution allowed
  Use    : Heavy grotesque block letters — Arial-Black-style chunky captions.
----------------------------------------------------------------------
BebasNeue-Regular.ttf       (logical key: `condensed`)
  Family : Bebas Neue
  Source : https://github.com/google/fonts/tree/main/ofl/bebasneue
  Design : Ryoichi Tsunekawa (Dharma Type)
  License: SIL Open Font License 1.1 — embedding & redistribution allowed
  Use    : Tall all-caps condensed — sleek headline / lower-third captions.
----------------------------------------------------------------------
