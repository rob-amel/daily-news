import streamlit as st
import requests
import json
from datetime import datetime, timedelta

# --- PLACEHOLDER PER LA TUA CHIAVE API DELLE NOTIZIE ---
try:
    NEWS_API_KEY = st.secrets["NEWS_API_KEY"]
except KeyError:
    st.error("⚠️ Chiave NEWS_API_KEY non trovata. Inseriscila in secrets.toml.")
    NEWS_API_KEY = "DUMMY_KEY"
    
BASE_NEWS_API_URL = "https://newsapi.org/v2/everything" # Esempio NewsAPI


def get_news_from_api(query, language='it', limit=5):
    """
    Funzione per chiamare l'API di notizie, filtrando per query.
    Restituisce una lista di articoli grezzi.
    """
    # Calcola l'ora di inizio 24 ore fa
    date_from = (datetime.now() - timedelta(hours=24)).strftime('%Y-%m-%d')
    
    params = {
        'qInTitle': query,
        'language': language,
        'sortBy': 'publishedAt',
        'from': date_from,
        'pageSize': limit,
        'apiKey': NEWS_API_KEY
    }
    
    try:
        response = requests.get(BASE_NEWS_API_URL, params=params, timeout=10)
        response.raise_for_status() # Solleva eccezioni per codici di stato di errore
        data = response.json()
        
        # Filtra solo i campi che interessano a Gemini per la sintesi
        articles = [{'title': a['title'], 'description': a['description'], 'url': a['url']}
                    for a in data.get('articles', []) if a['description'] and a['title']]
        return articles
        
    except requests.exceptions.RequestException as e:
        st.error(f"Errore nella connessione all'API per '{query}': {e}")
        return []
    except Exception as e:
        st.error(f"Errore generico nell'API per '{query}': {e}")
        return []


def run_news_collection(user_sources_list):
    """
    Esegue la raccolta delle notizie secondo l'ordine di importanza.
    """
    raw_digest_data = []
    
    # 1. Libano (Focus locale, usa l'italiano per la ricerca se vuoi fonti IT)
    lebanon_news = get_news_from_api(query="Libano", language='it', limit=7)
    raw_digest_data.append({"section": "Libano", "articles": lebanon_news})

    # 2. Gaza (Focus specifico)
    gaza_news = get_news_from_api(query="Gaza", language='it', limit=7)
    raw_digest_data.append({"section": "Gaza", "articles": gaza_news})

    # 3. Medio Oriente (Generale, esclusi i focus precedenti)
    me_news = get_news_from_api(query="(Medio Oriente OR Siria OR Palestina) NOT Gaza NOT Libano", language='it', limit=5)
    raw_digest_data.append({"section": "Medio Oriente", "articles": me_news})

    # 4. Italia politica interna
    italy_politics = get_news_from_api(query="Politica interna Italia", language='it', limit=5)
    raw_digest_data.append({"section": "Italia (Politica Interna)", "articles": italy_politics})
    
    # 5. Mondo (Notizie principali)
    world_news = get_news_from_api(query="Notizie principali", language='it', limit=5)
    raw_digest_data.append({"section": "Mondo (Principali)", "articles": world_news})
    
    # 6. (OPZIONALE) Fonti Personalizzate (Implementazione tramite un'altra API o libreria RSS)
    # L'API di notizie standard non supporta bene le fonti specifiche a meno di non fare chiamate multiple,
    # ma possiamo simulare la raccolta per l'invio a Gemini.
    if user_sources_list:
        # Nota: L'implementazione qui richiede una libreria RSS o uno scraper dedicato.
        # Per ora, usiamo la ricerca per nome dominio/sito come placeholder.
        custom_news = []
        for site in user_sources_list:
            # Chiamata API per trovare articoli da un dominio specifico (spesso supportato dagli aggregatori)
            site_articles = get_news_from_api(query=f"site:{site}", language='it', limit=3)
            custom_news.extend(site_articles)
            
        if custom_news:
            raw_digest_data.append({"section": "Fonti Personalizzate", "articles": custom_news})

    # Restituisce i dati grezzi pronti per essere sintetizzati da Gemini
    return raw_digest_data