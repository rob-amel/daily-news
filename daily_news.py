# ... (CODICE INIZIALE RIMANE UGUALE FINO A QUI) ...

# ----------------------------------------------------------------------
# --------------------- FUNZIONI DI GESTIONE DATI ----------------------
# ----------------------------------------------------------------------

def get_articles_via_rss(status_placeholder):
    """
    Legge TUTTI i feed RSS e raccoglie gli articoli recenti (ultime 48h).
    Aggiunto un controllo più aggressivo sul conteggio totale.
    """
    articles = []
    yesterday = datetime.now() - timedelta(hours=48) 
    
    status_placeholder.info("🔎 Tentativo di raccolta da tutti i feed RSS (ultime 48h)...")

    for url in RSS_FEED_URLS:
        try:
            feed = feedparser.parse(url)
            source = feed.feed.title if hasattr(feed.feed, 'title') else url.split('/')[2] 
            
            recent_count = 0
            # ... (Logica di raccolta RSS omessa per brevità, rimane uguale) ...
            
            for entry in feed.entries:
                is_recent = True
                if hasattr(entry, 'published_parsed') and entry.published_parsed:
                    published_date = datetime(*entry.published_parsed[:6])
                    if published_date < yesterday:
                        is_recent = False

                if is_recent:
                    description = getattr(entry, 'summary', None) or getattr(entry, 'description', None) or ""
                    articles.append({
                        'title': entry.title,
                        'description': description,
                        'url': entry.link,
                        'source': source,
                        'method': 'RSS'
                    })
                    recent_count += 1
            
            if recent_count > 0:
                 status_placeholder.markdown(f"&nbsp;&nbsp;✅ Trovati **{recent_count}** articoli da **{source}** (via RSS).")

        except Exception:
            continue

    total_articles = len(articles)
    if total_articles < 5:
        # Se i dati RSS sono scarsi, forziamo la sintesi a ignorarli (o usarli solo come base)
        status_placeholder.warning("⚠️ **ATTENZIONE:** RSS insufficienti (< 5 articoli). La sintesi sarà fortemente dipendente dalla Ricerca AI.")
    else:
        status_placeholder.success(f"✔️ Raccolta RSS completata: Totale **{total_articles}** articoli.")
        
    return articles

# run_news_collection rimane uguale

def summarize_with_gemini(rss_articles, search_queries, status_placeholder):
    """
    Invia gli articoli RSS e le query di ricerca a Gemini, con un prompt che forza
    l'esecuzione della ricerca e disincentiva l'affidamento all'RSS vuoto.
    """
    
    # --- 1. CONFIGURAZIONE CLIENTE (Ommessa, rimane uguale) ---
    try:
        client = genai.Client(api_key=GEMINI_API_KEY)
    except Exception:
        return None

    # --- 2. PREPARAZIONE DATI GREZZI E CONTROLLO ---
    
    # Se ci sono meno di 5 articoli RSS, li rimuoviamo dall'input JSON 
    # per forzare Gemini ad eseguire le query.
    if len(rss_articles) < 5:
        formatted_rss_json = "[]"
        rss_warning = "ATTENZIONE: Gli articoli RSS forniti erano insufficienti (< 5) e sono stati rimossi dall'input JSON per forzare la Ricerca AI."
    else:
        formatted_rss_json = json.dumps(rss_articles, indent=2)
        rss_warning = "Gli articoli RSS forniti sono sufficienti e inclusi."


    if not rss_articles and not search_queries:
         status_placeholder.error("⚠️ Nessun dato da processare (RSS vuoti e nessuna query di ricerca da eseguire).")
         return None 
        
    # --- 3. DEFINIZIONE DELLO SCHEMA JSON (Ommessa, rimane uguale) ---
    final_digest_schema = types.Schema(
        type=types.Type.OBJECT,
        properties={
            "script_tts": types.Schema(
                type=types.Type.STRING, 
                description="L'intero testo del radiogiornale. Deve essere lungo almeno 750 parole per garantire una durata di 5 minuti di parlato. Non usare titoli o elenchi."
            ),
            "titolo_digest": types.Schema(
                type=types.Type.STRING, 
                description="Un titolo conciso (massimo 10 parole) per il digest."
            )
        },
        required=["script_tts", "titolo_digest"]
    )
    
    # --- 4. PROMPT COMPLETO E CONFIGURAZIONE PER GEMINI ---
    
    sections_list = ", ".join(SECTIONS_MAPPING.keys())

    system_instruction = f"""
    Sei un giornalista radiofonico professionista e molto dettagliato. 
    
    **TASK PRIORITARIO: ESEGUIRE LE RICERCHE**
    A causa dell'inaffidabilità dei feed RSS, il tuo primo e fondamentale compito è **ESEGUIRE TUTTE LE QUERY** fornite utilizzando lo strumento Google Search Tool per raccogliere dati aggiornati e mirati dai siti richiesti.
    
    **TASK SECONDARIO:**
    1. **COMBINA** i risultati della Ricerca AI con gli articoli RSS (se presenti).
    2. **FILTRA** e **SINTETIZZA** le informazioni rilevanti per ciascuna delle seguenti sezioni, presentandole in questo ordine: {sections_list}.
    
    **REQUISITI:**
    * **LUNGHEZZA:** Lo script deve essere descrittivo e approfondito per raggiungere una lunghezza minima di 750 parole totali (circa 5 minuti di parlato).
    * **FORMATO:** Inizia con una breve introduzione e concludi con una chiusura. NON USARE titoli Markdown o elenchi puntati.
    """
    
    # ... (Configurazione e definizione del tool rimangono uguali) ...
    search_tool = types.Tool(google_search={}) 
    
    config = types.GenerateContentConfig(
        system_instruction=system_instruction,
        response_mime_type="application/json",
        response_schema=final_digest_schema,
        tools=[search_tool] 
    )

    prompt = f"""
    Genera lo script TTS (Text-to-Speech) basandoti sui dati combinati di RSS e ricerca.
    
    ---
    **AVVISO DATI RSS:** {rss_warning}
    ---
    
    ARTICOLI RSS TROVATI (Base dati iniziale, Formato JSON):
    ---
    {formatted_rss_json}
    ---
    
    QUERY DI RICERCA DA ESEGUIRE PER L'INTEGRAZIONE (Da usare con Google Search Tool):
    ---
    {json.dumps(search_queries, indent=2)}
    ---
    """
    
    status_placeholder.info("🧠 Avvio sintesi con Gemini AI. **Forzata l'esecuzione della Ricerca Mirata** per l'integrazione dati...")

    # ... (Chiamata API e gestione JSON omesse, rimangono uguali) ...
    try:
        response = client.models.generate_content(
            model='gemini-2.5-flash', 
            contents=prompt,
            config=config,
        )
        
        # Pulizia e decodifica del JSON
        json_string = response.text.strip()
        if json_string.startswith("```json"):
            json_string = json_string[7:]
        if json_string.endswith("```"):
            json_string = json_string[:-3]
            
        digest_data = json.loads(json_string)
        return digest_data

    except Exception as e:
        status_placeholder.error(f"❌ Errore durante la sintesi AI: {e}. Controlla i logs per dettagli.")
        return None
# ... (CODICE STREAMLIT RIMANE UGUALE FINO ALLA FINE) ...
