def summarize_with_gemini(rss_articles, search_queries, status_placeholder):
    """
    Invia gli articoli RSS e le query di ricerca a Gemini per filtrare, cercare e sintetizzare.
    """
    
    # --- 1. CONFIGURAZIONE CLIENTE ---
    try:
        client = genai.Client(api_key=GEMINI_API_KEY)
    except Exception:
        return None

    # --- 2. PREPARAZIONE DATI GREZZI E CONTROLLO ---
    
    # Dati grezzi RSS come lista JSON
    formatted_rss_json = json.dumps(rss_articles, indent=2)
    
    if not rss_articles and not search_queries:
         status_placeholder.error("⚠️ Nessun dato da processare (RSS vuoti e nessuna query di ricerca da eseguire).")
         return None 
        
    # --- 3. DEFINIZIONE DELLO SCHEMA JSON (Output TTS) ---
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
    Sei un giornalista radiofonico professionista e molto dettagliato. Il tuo compito è creare lo script per un radiogiornale.
    
    **TASK:**
    1. **ANALIZZA** gli articoli forniti tramite RSS.
    2. **ESEGUI** la ricerca utilizzando lo strumento Google Search Tool con le query fornite per trovare contenuti aggiuntivi e più specifici (ricerca mirata per dominio).
    3. **COMBINA** le informazioni trovate tramite RSS e Google Search.
    4. **FILTRA** e **SINTETIZZA** le informazioni rilevanti per ciascuna delle seguenti sezioni, presentandole in questo ordine: {sections_list}.
    
    **REQUISITI:**
    * **LUNGHEZZA:** Lo script deve essere descrittivo e approfondito per raggiungere una lunghezza minima di 750 parole totali (circa 5 minuti di parlato).
    * **TONO:** Neutro e informativo.
    * **FORMATO:** Inizia con una breve introduzione e concludi con una chiusura. NON USARE titoli Markdown o elenchi puntati. Il testo deve essere narrativo e fluido.
    """
    
    # --- CORREZIONE DEL VALIDATION ERROR QUI ---
    search_tool = types.Tool(google_search={})
    
    config = types.GenerateContentConfig(
        system_instruction=system_instruction,
        response_mime_type="application/json",
        response_schema=final_digest_schema,
        tools=[search_tool] # Passato come oggetto Tool
    )
    # ---------------------------------------------

    prompt = f"""
    Genera lo script TTS (Text-to-Speech) basandoti sui dati combinati di RSS e ricerca.
    
    ARTICOLI RSS TROVATI (Base dati iniziale, Formato JSON):
    ---
    {formatted_rss_json}
    ---
    
    QUERY DI RICERCA DA ESEGUIRE PER L'INTEGRAZIONE (Da usare con Google Search Tool):
    ---
    {json.dumps(SEARCH_QUERIES_TO_RUN, indent=2)}
    ---
    """
    
    status_placeholder.info("🧠 Avvio sintesi con Gemini AI. Richiesta integrazione ricerca e script di 5 minuti...")

    # --- 5. CHIAMATA ALL'API ---
    try:
        response = client.models.generate_content(
            model='gemini-2.5-flash', 
            contents=prompt,
            config=config,
        )
        
        # ... (Pulizia e decodifica del JSON)
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
