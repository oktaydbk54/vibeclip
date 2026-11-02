"""Blog content for vibeclip.dev — single source of truth for every article.

SEO-first: the index and article pages are SERVER-RENDERED from this list
(see blog_render.py), so search engines get real HTML + metadata, not a
client-side JS shell. Adding a post = appending one dict here.

Every body string is trusted, hand-authored HTML (no user input) — it is
injected verbatim into the page. Keep it valid and self-contained.
All copy is Spanish (product/marketing output is Spanish-only).
"""

import os

# Public base URL — drives canonical/sitemap/OG tags. Override with SITE_URL on
# a self-hosted instance (defaults to the hosted site).
SITE_URL = os.getenv("SITE_URL", "https://vibeclip.dev").rstrip("/")
SITE_NAME = os.getenv("SITE_NAME", "VibeClip")
OG_IMAGE = f"{SITE_URL}/static/icon-512.png"

# Canonical social/source profiles for the Organization entity. Single-sourced
# here so the blog's JSON-LD #org node never drifts from the landing page's.
REPO_URL = os.getenv("REPO_URL", "https://github.com/oktaydbk54/vibeclip")
SAME_AS = [REPO_URL, "https://x.com/codebod1"]

# Newest first. `date` is ISO (used for <time>, JSON-LD, sitemap lastmod).
POSTS = [
    {
        "slug": "vibeclip-vs-opus-clip",
        "title": "VibeClip vs. Opus Clip: Comparativa del editor de clips IA open-source",
        "description": "Opus Clip genera clips automáticamente en la nube con modelo de créditos/suscripción. VibeClip es la alternativa open-source, autoalojable y con tu propia clave LLM que editas hablando. Aquí tienes cómo difieren.",
        "keywords": "alternativa a opus clip, vibeclip vs opus clip, open source opus clip, generador de clips IA, editor de video IA gratis, creador de clips autoalojado",
        "tag": "COMPARAR",
        "read_min": 8,
        "date": "2026-06-13",
        "excerpt": "Opus Clip popularizó el auto-clipping de un clic. VibeClip aborda el mismo trabajo de vídeo largo a shorts y lo hace open source, autoalojable y editable por chat — con tu propia clave LLM.",
        "body": """
<p class="lede">Si has buscado una herramienta que convierta un vídeo largo en una pila de clips cortos con subtítulos, te has topado con <strong>Opus Clip</strong>. Hizo mainstream el auto-clipping de un clic. <strong>VibeClip</strong> aborda el mismo trabajo desde otro ángulo: es <strong>open source (AGPL-3.0)</strong>, puedes <strong>autoalojarlo</strong> y diriges la edición <strong>hablando</strong> con él en <em>tu propia</em> clave LLM.</p>

<p>No es un despiece — Opus Clip es un producto pulido. Es una comparación de dos filosofías para que elijas la que encaja en tu forma de trabajar.</p>

<h2>La diferencia clave: servicio cloud cerrado vs. estudio propio y abierto</h2>
<p>Opus Clip es un SaaS alojado. Subes a su nube, procesa en su infraestructura y pagas mediante créditos y niveles de suscripción. Es sin fricción — y también significa que tu material está en servidores ajenos y tus costes escalan con su precio.</p>
<p>VibeClip es software que puedes poseer de verdad. Usa la versión alojada, o clona el repo y haz <code>docker compose up</code> en tu máquina. Cuando te autoalojas, tu material nunca sale de tu equipo, el speech-to-text y el render corren localmente, y la única llamada saliente es al proveedor LLM <em>tú</em> eliges con la clave <em>tú</em> controlas.</p>

<h2>Cara a cara</h2>
<table>
  <thead><tr><th>&nbsp;</th><th>Opus Clip</th><th>VibeClip</th></tr></thead>
  <tbody>
    <tr><td>Modelo</td><td>SaaS alojado</td><td>Open source (AGPL-3.0) + alojado opcional</td></tr>
    <tr><td>Autoalojado</td><td>No</td><td>Sí — estudio completo, nada retenido</td></tr>
    <tr><td>Dónde vive tu vídeo</td><td>Su nube</td><td>Tu máquina (cuando te autoalojas)</td></tr>
    <tr><td>Precios</td><td>Créditos / suscripción</td><td>Gratis al autoalojar; pagas tus propios tokens LLM (céntimos por short)</td></tr>
    <tr><td>Cómo editas</td><td>Auto-clip y luego ajustes en UI</td><td>Auto-clip <em>y</em> refinas por chat: "corta los silencios", "subtítulos más grandes"</td></tr>
    <tr><td>Control sobre la IA</td><td>Sus modelos</td><td>Trae tu propia clave — OpenAI, Claude, Gemini, modelos locales</td></tr>
    <tr><td>Edits reversibles</td><td>Sí, en la app</td><td>Sí — cada cambio es un A/B que apruebas; nada destructivo</td></tr>
  </tbody>
</table>

<h2>Dónde gana VibeClip</h2>
<ul>
  <li><strong>Propiedad y privacidad.</strong> Autoalojar y tu material bruto y claves se quedan en infraestructura que controlas — la opción correcta para agencias, equipos regulados y cualquiera que prefiera no subir material de clientes a un tercero.</li>
  <li><strong>Coste a escala.</strong> Sin créditos por clip. Pagas a tu proveedor LLM directamente, que para la mayoría de creadores son unos céntimos por short en vez de una cuota mensual de clips.</li>
  <li><strong>Refinado por chat.</strong> El auto-clipping te lleva al 80%; el 20% restante es gusto. VibeClip te deja decir lo que quieres en lenguaje natural y monta un antes/después, así que no cazas por paneles. (Aquí tienes <a href="/blog/talk-to-edit-video-editing-by-chat">cómo funciona la edición por chat</a>.)</li>
  <li><strong>Sin vendor lock-in.</strong> Es AGPL — fórkalo, extiéndelo, ejecútalo para siempre.</li>
</ul>

<h2>Dónde un SaaS alojado sigue teniendo sentido</h2>
<p>Si nunca quieres pensar en infraestructura y prefieres una factura mensual fija a gestionar una clave LLM, una herramienta cloud llave en mano es genuinamente más simple el día uno. La versión alojada de VibeClip existe para eso — pero a diferencia de un producto cerrado, siempre puedes tomar la build open-source e irte.</p>

<h2>La conclusión</h2>
<p>Opus Clip es un gran producto cerrado para auto-clipping cloud sin manos. VibeClip es para creadores que quieren la misma velocidad de vídeo-largo-a-shorts <em>más</em> propiedad, privacidad, menor coste operativo y un editor con el que pueden hablar. ¿Nuevo en la categoría? Empieza con <a href="/blog/what-is-an-ai-video-editor">qué es un editor de vídeo IA</a>, o directamente <a href="/signup">prueba VibeClip gratis</a> y convierte tu próximo vídeo largo en shorts.</p>
""",
    },
    {
        "slug": "open-source-vs-saas-video-editor",
        "title": "Open-Source vs. SaaS edición de vídeo: por qué autoalojar gana para creadores",
        "description": "Las herramientas cloud de vídeo son cómodas pero alquiler para siempre y suben tu material a servidores ajenos. Aquí tienes el caso a favor de un editor de vídeo IA gratis, open-source y autoalojado — y cuándo el SaaS sigue encajando.",
        "keywords": "editor de video open source gratis, editor de video autoalojado, edicion de video open source, editor de video privado, editor de video agpl, editor de video byok",
        "tag": "COMPARAR",
        "read_min": 7,
        "date": "2026-06-13",
        "excerpt": "Comodidad vs. propiedad. Por qué un editor IA open-source y autoalojado gana en privacidad y coste a una herramienta cloud alquilada — y los casos donde SaaS sigue siendo la opción correcta.",
        "body": """
<p class="lede">Casi todas las herramientas de vídeo IA hoy son <strong>SaaS</strong>: subes tu material a la nube, procesa en sus servidores y pagas una factura recurrente. Es cómodo — y silenciosamente renuncia a dos cosas que los creadores valoran cada vez más: <strong>propiedad</strong> y <strong>privacidad</strong>. Aquí tienes el caso honesto a favor de la alternativa open-source y autoalojada.</p>

<h2>Qué significa realmente "autoalojado"</h2>
<p>Una app autoalojada corre en infraestructura <em>tú</em> controlas — tu portátil, tu VPS, el servidor de tu empresa. Con un editor de vídeo IA open-source como VibeClip, eso significa que tus vídeos en bruto, transcripciones y renders nunca salen de tu máquina. Speech-to-text y cada render corren localmente; lo único que toca la red es el proveedor LLM que eliges, llamado con una clave que posees (ese es el modelo "bring your own key" / BYOK).</p>

<h2>Tres razones por las que gana</h2>
<ul>
  <li><strong>Privacidad por defecto.</strong> Material sin publicar, grabaciones de clientes, grabaciones internas — nada se sube a un tercero. Para agencias y equipos regulados no es un nice-to-have, es un requisito.</li>
  <li><strong>Coste que no se compone.</strong> El precio SaaS escala con <em>sus</em> márgenes y tu nivel de uso. Autoalojar es gratis; pagas a tu proveedor LLM directamente por tokens — típicamente unos céntimos por short. Sin créditos por clip, sin tasas por asiento.</li>
  <li><strong>Sin lock-in, nunca.</strong> Open source (VibeClip es AGPL-3.0) significa que puedes leer el código, forkearlo, extenderlo y seguir ejecutándolo aunque la empresa detrás desaparezca. Un SaaS cerrado puede cambiar precios, poner features detrás de paywall, o cerrar — y tu workflow se va con él.</li>
</ul>

<h2>El impuesto de la comodidad</h2>
<p>Seamos justos: SaaS es más fácil el día uno. Nada que instalar, ninguna clave que gestionar, ningún servidor que mantener vivo. Para un creador que edita ocasionalmente y prefiere una cuota mensual predecible, esa simplicidad vale dinero real.</p>
<p>La respuesta open-source a "pero configurar es difícil" es hacer la configuración trivial. VibeClip corre en el navegador, y autoalojarse es un <code>docker compose up</code>. Añades tu clave LLM una vez y ya estás editando. (¿Curioso de cómo se siente la edición? <a href="/blog/talk-to-edit-video-editing-by-chat">Hablas con él.</a>)</p>

<h2>Cuándo el SaaS sigue teniendo sentido</h2>
<p>Si tienes cero interés en infraestructura, editas poco y quieres una factura única con soporte, un producto gestionado es una opción razonable. Lo mejor de los dos mundos es una herramienta que ofrezca opción alojada <em>y</em> build open-source — así empiezas fácil y puedes tomar propiedad cuando superes la nube.</p>

<h2>Cómo encaja VibeClip</h2>
<p>VibeClip es open source bajo AGPL-3.0, autoalojable en un comando, y BYOK — y hay versión alojada para quien quiere llave en mano. Mismo estudio en ambos casos; ninguna feature retenida para el tier de pago. Si estás sopesando herramientas, aquí tienes un cara a cara concreto: <a href="/blog/vibeclip-vs-opus-clip">VibeClip vs. Opus Clip</a>. O simplemente <a href="/signup">pónlo a andar gratis</a> y mira cómo <a href="/blog/turn-long-video-into-shorts-with-ai">un vídeo largo se convierte en una semana de shorts</a>.</p>
""",
    },
    {
        "slug": "capcut-alternative-ai-video-editor",
        "title": "Una alternativa a CapCut para creadores que odian la timeline",
        "description": "CapCut es potente pero sigue siendo un editor de timeline manual. Si reutilizas vídeos largos en shorts, un editor IA talk-to-edit como VibeClip es más rápido — y es open source. Aquí la comparativa.",
        "keywords": "alternativa a capcut, capcut para podcasts, editor de video ia vs capcut, editor de video sin timeline, reutilizar video largo, alternativa open source a capcut",
        "tag": "COMPARAR",
        "read_min": 7,
        "date": "2026-06-13",
        "excerpt": "CapCut te da todas las perillas — y cada perilla es tu trabajo. Para convertir grabaciones largas en shorts, un editor IA talk-to-edit elimina la timeline por completo. Así se compara VibeClip.",
        "body": """
<p class="lede"><strong>CapCut</strong> es el editor móvil/escritorio por defecto para un número enorme de creadores, y con razón — es gratis para empezar, rico en features y genial para trabajo manual a nivel de frame. Pero si tu trabajo real es <em>reutilizar grabaciones largas en shorts</em>, CapCut sigue pidiéndote que hagas la edición. <strong>VibeClip</strong> es la alternativa que hace la edición por ti — solo le hablas.</p>

<h2>Dos trabajos distintos</h2>
<p>CapCut es un <strong>editor de timeline manual</strong> con features IA añadidas. Importas clips, los colocas en una timeline y controlas tú cada transición, subtítulo y corte. Es perfecto cuando quieres control creativo total sobre una pieza.</p>
<p>VibeClip está construido para el workflow <strong>vídeo-largo-a-shorts</strong> específicamente. Sueltas una hora de podcast o stream, y encuentra los momentos publicables, reencuadra a vertical, subtitula y aprieta el ritmo — luego refinas describiendo lo que quieres. No hay timeline que scrubbear. (¿Nuevo en la idea? <a href="/blog/what-is-an-ai-video-editor">Aquí tienes qué hace un editor de vídeo IA.</a>)</p>

<h2>Cara a cara</h2>
<table>
  <thead><tr><th>&nbsp;</th><th>CapCut</th><th>VibeClip</th></tr></thead>
  <tbody>
    <tr><td>Modelo de edición</td><td>Timeline manual + herramientas IA</td><td>Talk-to-edit; la IA hace el trabajo, tú apruebas</td></tr>
    <tr><td>Mejor para</td><td>Edits manuales a nivel de frame</td><td>Convertir vídeos largos en muchos shorts rápido</td></tr>
    <tr><td>Encuentra clips por ti</td><td>No — tú scrubbeas y cortas</td><td>Sí — detección de highlights propone los momentos</td></tr>
    <tr><td>Open source / autoalojado</td><td>No</td><td>Sí — AGPL-3.0, corre en tu servidor</td></tr>
    <tr><td>Tu material</td><td>Procesado por la app/nube</td><td>Se queda en tu máquina al autoalojar</td></tr>
    <tr><td>Curva de aprendizaje</td><td>Real — es un NLE completo</td><td>Describes la edición en lenguaje natural</td></tr>
  </tbody>
</table>

<h2>Cuándo tirar de cada uno</h2>
<p>Queda CapCut para edits hero puntuales donde quieres colocar cada elemento a mano. Tira de VibeClip cuando tienes un backlog de grabaciones largas y necesitas sacar shorts a ritmo — batchar una semana de contenido de una subida es exactamente el caso que las timelines hacen lento. (Ver <a href="/blog/repurpose-one-video-into-a-week-of-shorts">cómo reutilizar un vídeo en una semana de shorts</a>.)</p>

<h2>Por qué los creadores cambian para reutilizar</h2>
<ul>
  <li><strong>Velocidad.</strong> Sin timeline no hay scrubbing. El cuello de botella — encontrar y cortar los buenos momentos — es lo que la IA gestiona.</li>
  <li><strong>Propiedad.</strong> VibeClip es open source y autoalojable; tu material y tu clave LLM se quedan tuyos. (Más en <a href="/blog/open-source-vs-saas-video-editor">open source vs. SaaS</a>.)</li>
  <li><strong>Aprobación, no rendición.</strong> Cada edición IA se monta como un antes/después que aceptas o rechazas — mantienes control creativo sin el trabajo manual.</li>
</ul>

<h2>Pruébalo en tu próximo vídeo largo</h2>
<p>Si las timelines son la parte de editar que temes, esa es exactamente la parte que VibeClip elimina. <a href="/signup">Empieza gratis</a>, suelta una grabación larga y describe los shorts que quieres.</p>
""",
    },
    {
        "slug": "what-is-an-ai-video-editor",
        "title": "Qué es un editor de vídeo IA: cómo convierte vídeos largos en shorts",
        "description": "Un editor de vídeo IA entiende qué hay dentro de tu material y hace la edición por ti — subtítulos, reencuadre, cortes. Así funciona y cómo pasar de un vídeo largo a shorts listos para viralizar.",
        "keywords": "editor de video ia, que es un editor de video ia, edicion de video ia, video largo a shorts, edicion de video automatica",
        "tag": "GUÍA",
        "read_min": 7,
        "date": "2026-06-12",
        "excerpt": "La explicación en llano: qué hace realmente un editor de vídeo IA, qué no, y por qué es la forma más rápida de convertir una grabación larga en una pila de shorts.",
        "body": """
<p class="lede">Un <strong>editor de vídeo IA</strong> es software que entiende qué hay <em>dentro</em> de tu material — las palabras, el hablante, las pausas, los highlights — y hace el trabajo mecánico de edición por ti. En lugar de arrastrar clips en una timeline durante horas, le apuntas a un vídeo largo y te devuelve shorts verticales terminados, subtitulados y reencuadrados, listos para publicar.</p>

<p>Durante la última década, "edición de vídeo" significaba una timeline, un playhead y mil microdecisiones manuales. La IA cambia la unidad de trabajo: dejas de editar <em>frames</em> y empiezas a editar <em>intención</em>. Dices lo que quieres; el editor averigua los frames.</p>

<h2>Qué hace realmente un editor de vídeo IA</h2>
<p>Por debajo, un editor IA moderno encadena unos modelos especializados. Cada uno gestiona un trabajo que era manual:</p>
<ul>
  <li><strong>Transcripción.</strong> Speech-to-text convierte tu audio en transcripción alineada temporalmente. Es la columna vertebral — una vez que el editor sabe <em>qué se dice cuándo</em>, puede cortar, subtitular y buscar por significado.</li>
  <li><strong>Detección de highlights.</strong> Puntúa la transcripción para encontrar los momentos con más probabilidad de viajar — un hook fuerte, un punchline, una conclusión limpia — y los propone como clips.</li>
  <li><strong>Reencuadre inteligente.</strong> Rastrea al hablante y recorta un frame 16:9 landscape a 9:16 vertical, manteniendo la cara centrada en vez de cortarla.</li>
  <li><strong>Subtítulos.</strong> Quema subtítulos palabra por palabra que se mantienen en sincro — el mayor driver de watch time en feeds muteados.</li>
  <li><strong>Limpieza.</strong> Eliminación de silencios, recorte de muletillas y ajuste de ritmo para que el clip se sienta tenso, no en bruto.</li>
</ul>

<h2>Por qué "vídeo largo a shorts" es el caso killer</h2>
<p>La mayoría de creadores ya están sentados sobre una mina: podcasts, streams, webinars, charlas, entrevistas. Una hora de talking-head contiene fácilmente de cinco a quince momentos publicables. Encontrar y cortar esos a mano es el cuello de botella — no el talento, no la cámara. Un editor de vídeo IA colapsa ese cuello de una tarde a minutos.</p>
<p>El workflow se ve así: sueltas el archivo largo → el editor transcribe y encuentra clips candidatos → eliges los que te gustan → reencuadra, subtitula y exporta cada uno como short vertical. Una grabación se convierte en una semana de contenido.</p>

<h2>Qué NO hace (y no debería)</h2>
<p>Un editor de vídeo IA no sustituye al gusto. Puede encontrar un momento fuerte, pero no conoce tu voz de marca, tus bromas internas, o la frase que a ti te importa personalmente. Las mejores herramientas te mantienen en el bucle: <em>proponen</em>, tú <em>apruebas</em>. La edición sigue siendo reversible, y nada sale sin un asentimiento humano.</p>

<h2>Cómo lo aborda VibeClip</h2>
<p>VibeClip es un editor de vídeo IA que diriges <strong>hablando</strong>. Describes la edición en lenguaje natural — "corta los silencios", "agrandar subtítulos", "saca los mejores 30 segundos" — y monta un antes/después para que apruebes cada cambio. Sin scrubbing de timeline, sin keyframes, sin instalación. <a href="/signup">Empieza gratis</a> y convierte tu próximo vídeo largo en shorts en una sentada.</p>
""",
    },
    {
        "slug": "turn-long-video-into-shorts-with-ai",
        "title": "Cómo convertir un vídeo largo en shorts con IA (paso a paso)",
        "description": "Un workflow simple y repetible para pasar de un vídeo largo a múltiples shorts verticales con IA — de la subida al export subtitulado y reencuadrado.",
        "keywords": "convertir video largo en shorts, video largo a shorts ia, como hacer shorts de videos largos, generador de clips ia, reutilizar video",
        "tag": "TUTORIAL",
        "read_min": 6,
        "date": "2026-06-11",
        "excerpt": "De una sola grabación larga a una pila de clips verticales listos para publicar — el paso a paso exacto, y las tres decisiones que hacen o rompen cada clip.",
        "body": """
<p class="lede">Ya grabaste la cosa larga — el podcast, el stream, la charla. La parte dura nunca fue la grabación; es trocearla en shorts que la gente vea de verdad. Aquí tienes el paso a paso con un <strong>editor de vídeo IA</strong>, y las pocas decisiones de juicio que importan.</p>

<h2>Paso 1 — Empieza con la fuente adecuada</h2>
<p>El mejor material en bruto es footage <strong>impulsado por habla</strong>: entrevistas, podcasts, charlas, reaction streams, tutoriales. Cualquier cosa donde el valor viva en lo que se <em>dice</em>. Una pista de voz clara importa más que 4K — subtítulos y detección de clips cabalgan sobre la transcripción, así que audio limpio gana a imagen bonita.</p>

<h2>Paso 2 — Deja que la IA encuentre los momentos</h2>
<p>Sube el vídeo completo y deja que el editor transcriba y escanee. En lugar de scrubbear una hora buscando oro, obtienes una lista corta de clips candidatos rankeados por probabilidad de pegar. Cada uno suele centrar en una idea: un hook, una opinión fuerte, un how-to limpio.</p>
<p><strong>Tip:</strong> apunta a clips que se entiendan con cero contexto. Si un momento solo tiene sentido si viste los diez minutos previos, no sobrevivirá al feed.</p>

<h2>Paso 3 — Reencuadra a vertical (9:16)</h2>
<p>Shorts, Reels y TikTok viven en frame vertical 9:16. Un center-crop ingenuo corta cabezas en cuanto alguien se mueve. El reencuadre inteligente rastrea al hablante y mueve el crop 9:16 para seguirle — la diferencia entre "hecho para vertical" y "obviamente un landscape troceado".</p>

<h2>Paso 4 — Añade subtítulos</h2>
<p>La mayoría de feeds autoplayan en mute. Subtítulos palabra por palabra quemados son la edición de mayor apalancamiento — rutinariamente levantan watch time porque el espectador puede seguir sin sonido. Mantenlos grandes, alto contraste, y sincronizados a pelo con el audio.</p>

<h2>Paso 5 — Aprieta el ritmo</h2>
<p>El habla en bruto está llena de pausas, "eh"s y aire muerto. En un short, cada segundo muerto es una rampa de salida — razón para seguir scroll. Elimina aire muerto, pausas largas y muletillas. Un short debe sentirse que se inclina hacia adelante. Solo cortar silencios ya recorta 20–30% de un clip en bruto y mejora retención notablemente.</p>

<h2>Paso 6 — Revisa y exporta</h2>
<p>Antes de exportar, mira el clip una vez a velocidad real. ¿El primer segundo gana los siguientes cinco? ¿El payoff está realmente en frame? Los buenos editores IA montan cada cambio como propuesta reversible, así que puedes retocar el hook o recortar el final antes de que nada sea final. Exporta en 1080p vertical limpio y listo para publicar.</p>

<h2>Las tres decisiones que más importan</h2>
<ol>
  <li><strong>El hook.</strong> Los primeros 1–2 segundos lo deciden todo. Arranca en la línea más arrestante, no en el calentamiento.</li>
  <li><strong>La duración.</strong> Más corta de lo que crees. Si puede ser 22 segundos, no la hagas 40.</li>
  <li><strong>El payoff.</strong> Cada clip necesita una razón por la que mereció la pena verlo — una risa, una lección, un "ajá".</li>
</ol>

<p>VibeClip corre todo esto desde una sola caja de chat: describes lo que quieres y apruebas cada resultado. <a href="/signup">Pruébalo gratis</a> en tu próxima grabación larga.</p>
""",
    },
    {
        "slug": "talk-to-edit-video-editing-by-chat",
        "title": "Talk-to-Edit: editar vídeo por chat en vez de timeline",
        "description": "Talk-to-edit te deja editar vídeo con instrucciones en lenguaje natural en vez de una timeline. Qué cambia, dónde brilla, y dónde una frase sigue ganando al ratón.",
        "keywords": "hablar para editar, editor de video por chat, editar video con texto, edicion de video lenguaje natural, editor de video ia chat",
        "tag": "OFICIO",
        "read_min": 6,
        "date": "2026-06-10",
        "excerpt": "Qué pasa cuando el cuarto de edición escucha en vez de esperar keyframes — y los sitios sorprendentes donde una sola frase supera a una timeline.",
        "body": """
<p class="lede">La timeline lleva mandando en edición de vídeo desde los 90: una tira horizontal, un playhead, y tú, empujando clips frame a frame. <strong>Talk-to-edit</strong> propone un contrato distinto — dices lo que quieres en lenguaje natural y el editor hace el trabajo a nivel de frame. Es menos "opera el software" y más "dirige la edición".</p>

<h2>Qué cambia realmente</h2>
<p>Con una timeline, el hueco entre <em>saber</em> lo que quieres y <em>conseguirlo</em> está lleno de micro-skills: ripple deletes, keyframes, gestión de tracks, presets de export. Cada una es aprendible, pero cada una es fricción. Talk-to-edit elimina la capa de fricción:</p>
<ul>
  <li>"Corta los primeros 20 segundos" en vez de arrastrar el in-point.</li>
  <li>"Añade subtítulos y agrándalos" en vez de abrir una herramienta de títulos.</li>
  <li>"Saca los mejores 30 segundos" en vez de scrubbear una hora.</li>
  <li>"Hazlo más punchy" en vez de recortar manualmente cada pausa.</li>
</ul>
<p>La intención siempre estuvo en tu cabeza. Talk-to-edit solo te deja expresarla directo.</p>

<h2>Dónde brilla</h2>
<p><strong>Edits repetitivos y describibles.</strong> Subtítulos, eliminación de silencios, reencuadre, ritmo, conversión de formato — cualquier cosa que podrías explicarle a un asistente en una frase es más rápido dicho que clicado. Para pasar de vídeo largo a shorts, donde repites el mismo puñado de operaciones en muchos clips, el speedup es dramático.</p>
<p><strong>Principiantes.</strong> No hay UI que aprender. El primer edit de un usuario nuevo es tan rápido como el centésimo, porque la interfaz es lenguaje que ya habla.</p>

<h2>Dónde una frase sigue perdiendo contra el ratón</h2>
<p>Talk-to-edit no es magia, y las buenas herramientas son honestas al respecto. Trabajo creativo frame-perfecto — un corte sincronizado a música preciso, un motion graphic afinado a mano, color grading a ojo — sigue siendo más rápido y mejor con manipulación directa. El modelo correcto es <strong>híbrido</strong>: habla para el 90% que es describible, tira de controles finos en el 10% que no lo es.</p>

<h2>El problema de la confianza — y la solución</h2>
<p>La preocupación obvia: si la IA edita, ¿cómo sé que hizo lo que quise? La respuesta es <strong>propuesta, no autopiloto</strong>. Cada instrucción debería producir un cambio montado que ves — un antes/después — que apruebas o rechazas. Nada destructivo, historial completo, siempre reversible. Eso es lo que hace talk-to-edit confiable en vez de una apuesta.</p>

<p>VibeClip está construido enteramente alrededor de esto: editas chateando, en lenguaje natural, y apruebas cada resultado antes de que se quede. <a href="/signup">Empieza gratis</a> y edita tu próximo clip hablándole.</p>
""",
    },
    {
        "slug": "auto-captions-smart-reframe-silence-removal",
        "title": "Subtítulos auto, reencuadre inteligente y eliminación de silencios: la pila de edición IA",
        "description": "Las tres ediciones IA que convierten footage en bruto en shorts que paran el scroll — subtítulos auto, reencuadre inteligente y eliminación de silencios — explicadas simple.",
        "keywords": "subtitulos auto, reencuadre inteligente, eliminacion de silencios, subtitulos ia video, reencuadre video vertical, features edicion video ia",
        "tag": "EXPLICADOR",
        "read_min": 5,
        "date": "2026-06-09",
        "excerpt": "Tres ediciones hacen el 90% del trabajo pesado en cada short viral. Qué es cada una, por qué funciona, y cómo la IA la hace automática.",
        "body": """
<p class="lede">Desnuda un short viral a su mecánica y encontrarás las mismas tres ediciones casi siempre: <strong>subtítulos</strong>, <strong>un reencuadre vertical</strong> y <strong>ritmo tenso</strong>. Cada una era manual. Un <strong>editor de vídeo IA</strong> hace las tres automáticas. Aquí tienes qué son y por qué importan.</p>

<h2>1. Subtítulos automáticos</h2>
<p>La mayoría del vídeo social se ve en mute — en la cama, en transporte, en una reunión. Subtítulos quemados, palabra por palabra, dejan al espectador seguir con cero sonido, por eso los clips subtitulados retienen consistentemente más atención que los que no.</p>
<p><strong>Cómo lo hace la IA:</strong> speech-to-text transcribe el audio con timestamps por palabra, y luego renderiza cada palabra en pantalla exactamente cuando se dice. El resultado es el subtítulo estilo karaoke que ves en casi todo short exitoso — y como va pilotado por la transcripción, se mantiene en sincro incluso después de cortar. Los buenos editores tratan los subtítulos como <em>sagrados</em>: cortes y cambios de velocidad nunca deben sacarlos de alineación.</p>

<h2>2. Reencuadre inteligente (16:9 → 9:16)</h2>
<p>Tu cámara grabó landscape. El feed quiere vertical. Un center-crop tonto pierde a cualquiera que no esté muerto en el centro — y corta cabezas en cuanto alguien se mueve.</p>
<p><strong>Cómo lo hace la IA:</strong> el editor detecta y rastrea al hablante, y mueve la ventana de crop 9:16 para seguirle por el plano. ¿Dos personas hablando? Puede cortar entre ellas. La salida parece <em>grabada</em> para vertical, no recortada torpe de otra cosa — el detalle que separa shorts pro de reutilizaciones obvias.</p>

<h2>3. Eliminación de silencios y ritmo</h2>
<p>El habla en bruto está llena de pausas, "eh"s y aire muerto. En un short, cada segundo muerto es una rampa de salida — razón para seguir scroll.</p>
<p><strong>Cómo lo hace la IA:</strong> usando la misma transcripción con tiempos por palabra, el editor encuentra huecos y muletillas y los recorta, apretando el clip sin que suene troceado. Eliminar silencios solo suele recortar 20–30% del runtime y mejora visiblemente la retención — el clip se inclina hacia adelante en vez de arrastrarse.</p>

<h2>Por qué se componen</h2>
<p>Ninguna de las tres impresiona sola. Juntas son el juego entero: subtítulos mantienen a espectadores muteados, reencuadre lo hace sentir nativo, y ritmo tenso frena el scroll. Apiladas y automatizadas, convierten una grabación en bruto de 45 minutos en un short vertical limpio en minutos en vez de una tarde.</p>

<p>VibeClip corre la pila completa desde una caja de chat — subtítulos, reencuadre y eliminación de silencios a petición, cada uno montado para tu aprobación. <a href="/signup">Pruébalo gratis</a>.</p>
""",
    },
    {
        "slug": "repurpose-one-video-into-a-week-of-shorts",
        "title": "Reutiliza un vídeo largo en una semana de shorts",
        "description": "Un sistema repetible para convertir una sola grabación larga en una semana de shorts verticales — sin quemarte ni quedarte sin ideas.",
        "keywords": "reutilizar video, repurposing contenido, un video en muchos shorts, crear shorts en lote, workflow creadores contenido",
        "tag": "CRECIMIENTO",
        "read_min": 6,
        "date": "2026-06-08",
        "excerpt": "Postear diario no significa grabar diario. El sistema de batching que convierte una grabación en siete posts — y evita que el pozo se seque.",
        "body": """
<p class="lede">Los creadores que postean cada día casi nunca graban cada día. Están <strong>reutilizando</strong> — convirtiendo una grabación larga en muchas cortas. Aquí tienes el sistema, y por qué es el movimiento de growth más infravalorado para cualquiera con catálogo trasero.</p>

<h2>La matemática que lo cambia todo</h2>
<p>Una hora de buen footage talking-head aguanta, conservadoramente, de cinco a diez momentos independientes. Graba una vez a la semana y tienes material en bruto para postear un short cada día — sin volver a apuntar una cámara a ti una segunda vez. La restricción nunca fueron ideas; fueron las horas que costaba encontrarlos y cortarlos. Un <strong>editor de vídeo IA</strong> elimina esa restricción.</p>

<h2>El sistema de batching semanal</h2>
<ol>
  <li><strong>Graba una pieza ancla.</strong> Un podcast, un livestream, una charla larga — cualquier cosa de 30–90 minutos donde seas genuinamente interesante.</li>
  <li><strong>Mina momentos.</strong> Pásalo por un editor IA y recoge cada clip candidato. No te auto-edites aún — recopila primero.</li>
  <li><strong>Elige siete.</strong> Quédate con los momentos independientes más fuertes. Cada uno debe funcionar con cero contexto.</li>
  <li><strong>Corta, subtitula, reencuadra en una sentada.</strong> Batea las mismas operaciones en los siete. Aquí es donde la IA ahorra horas — el trabajo es idéntico por clip.</li>
  <li><strong>Programa la semana.</strong> Un clip al día. Ya estás "posteando diario" con una tarde de trabajo.</li>
</ol>

<h2>Evitando que el pozo se seque</h2>
<p>Reutilizar falla cuando cada clip se siente igual. Varía el <em>ángulo</em>, no solo el timestamp: saca un momento didáctico, una opinión fuerte, un behind-the-scenes, un one-liner, una historia. La misma grabación puede alimentar cinco "carriles" de contenido distintos si los buscas.</p>

<h2>Cross-post, no clones</h2>
<p>El mismo short vertical encaja en Shorts, Reels y TikTok — pero ajusta el framing por plataforma: un hook ligeramente distinto, un caption afinado a cada audiencia. Mismo clip núcleo, feel nativo en todas.</p>

<h2>La recompensa</h2>
<p>Reutilizar convierte el volumen de un grind en un sistema. Filmas cuando estás a tope, luego pasas una sesión enfocada convirtiendo eso en una semana de posts. La consistencia deja de depender de motivación y empieza a depender de un workflow — que es la única forma de que sobreviva lo suficiente para componer.</p>

<p>VibeClip está construido exactamente para este bucle: sueltas el vídeo largo, sacas los clips, subtitulas y reencuadras por chat, exportas el set. <a href="/signup">Empieza gratis</a> y convierte tu catálogo trasero en motor de contenido.</p>
""",
    },
]

POSTS_BY_SLUG = {p["slug"]: p for p in POSTS}


def get_post(slug: str):
    return POSTS_BY_SLUG.get(slug)