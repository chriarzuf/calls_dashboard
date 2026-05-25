import streamlit as st
import pandas as pd
import numpy as np
import holidays
from pandas.tseries.offsets import CustomBusinessDay

# ==========================================
# CONFIGURAZIONE PAGINA
# ==========================================
st.set_page_config(page_title="Dashboard SLA Aircall v6.2", layout="wide")
st.title("📊 Dashboard Analisi SLA Inbound - Aircall")

# ==========================================
# CALENDARIO FESTIVI ITALIANI AUTOMATICO
# ==========================================
anni_interesse = [2024, 2025, 2026, 2027]
festivi_it = holidays.IT(years=anni_interesse)
festivi_italiani = list(festivi_it.keys())
it_bday = CustomBusinessDay(holidays=festivi_italiani)

# ==========================================
# FUNZIONI DI SUPPORTO PER GLI ORARI
# ==========================================
def parse_time_to_float(time_str):
    time_str = str(time_str).strip()
    if ':' in time_str:
        h, m = time_str.split(':')
        return int(h) + (int(m) / 60.0)
    return float(time_str)

def format_float_to_time(time_float):
    h = int(time_float)
    m = int(round((time_float - h) * 60))
    return f"{h:02d}:{m:02d}"

# ==========================================
# 1. SIDEBAR: IMPOSTAZIONI E CARICAMENTO
# ==========================================
st.sidebar.header("⚙️ Impostazioni Generali")
uploaded_file = st.sidebar.file_uploader("Carica il file CSV di Aircall", type=["csv"])

st.sidebar.subheader("Configurazione Fasce Orarie")
bins_input = st.sidebar.text_input("Fasce (separate da virgola)", "9, 10:30, 12:30, 14, 16, 18")

try:
    orari_fasce_float = [parse_time_to_float(x) for x in bins_input.split(',')]
    if len(orari_fasce_float) < 2:
        st.sidebar.error("Inserisci almeno due orari per creare una fascia.")
    etichette_fasce = [
        f"{format_float_to_time(orari_fasce_float[i])} - {format_float_to_time(orari_fasce_float[i+1])}" 
        for i in range(len(orari_fasce_float)-1)
    ]
except ValueError:
    st.sidebar.error("Formato non valido. Esempio corretto: 9, 10:30, 12, 14")

# ==========================================
# 2. MOTORE DI PREPARAZIONE DATI GREZZI
# ==========================================
@st.cache_data
def load_and_process_data(file):
    df = pd.read_csv(file, sep=None, engine='python')
    df.columns = df.columns.str.strip()
    
    # FIX DATE: dayfirst=True forza la lettura europea (GG/MM/AAAA)
    df['datetime'] = pd.to_datetime(df['datetime (tz offset incl.)'], dayfirst=True)
    
    df['customer_number'] = np.where(df['direction'] == 'inbound', df['from'], df['to'])
    df['waiting_seconds'] = pd.to_timedelta(df['waiting time']).dt.total_seconds().fillna(0)
    
    is_ghost = (df['direction'] == 'inbound') & (df['answered'] == 'No') & (
        (df['waiting_seconds'] <= 5) | (df['missed_call_reason'] == 'short_abandoned')
    )
    
    ghosts_df = df[is_ghost].copy()
    inbound_df = df[(df['direction'] == 'inbound') & (~is_ghost)].copy()
    
    valid_contacts = df[
        (df['direction'] == 'outbound') | 
        ((df['direction'] == 'inbound') & (df['answered'] == 'Yes'))
    ].copy()
    
    valid_contacts['datetime_risoluzione'] = valid_contacts['datetime']
    valid_contacts['advisor_risoluzione'] = valid_contacts['user']
    
    inbound_df.sort_values('datetime', inplace=True)
    valid_contacts.sort_values('datetime', inplace=True)
    
    merged_df = pd.merge_asof(
        inbound_df, 
        valid_contacts[['datetime', 'customer_number', 'datetime_risoluzione', 'advisor_risoluzione']], 
        on='datetime', 
        by='customer_number', 
        direction='forward'
    )
    
    if 'datetime_risoluzione' not in merged_df.columns:
        merged_df['datetime_risoluzione'] = pd.NaT
    if 'advisor_risoluzione' not in merged_df.columns:
        merged_df['advisor_risoluzione'] = 'Non Gestita'
        
    return merged_df, ghosts_df

def applica_regole_sla(row):
    if row['answered'] == 'Yes':
        return 'Verde', row['user']
    if pd.isnull(row['datetime_risoluzione']):
        return 'Rosso', 'Non Gestita'
        
    call_time = row['datetime']
    resolve_time = row['datetime_risoluzione']
    
    is_weekend = call_time.weekday() >= 5
    is_holiday = call_time.date() in festivi_italiani
    is_business_day = not is_weekend and not is_holiday
    is_business_hours = 9 <= call_time.hour < 18
    
    if is_business_day and is_business_hours:
        delta_seconds = (resolve_time - call_time).total_seconds()
        esito = 'Recuperata' if delta_seconds <= 3600 else 'Rosso'
    else:
        if not is_business_day or call_time.hour >= 18:
            next_bday = pd.Timestamp(call_time.date()) + it_bday
        else: 
            next_bday = pd.Timestamp(call_time.date())
            
        deadline = next_bday.replace(hour=10, minute=0, second=0)
        esito = 'Recuperata' if resolve_time <= deadline else 'Rosso'
        
    advisor_assegnato = row['advisor_risoluzione'] if esito == 'Recuperata' else 'In Ritardo'
    return esito, advisor_assegnato

# ==========================================
# 3. FILTRO GLOBALE E APPLICAZIONE REGOLE
# ==========================================
if uploaded_file is not None:
    try:
        df_merged_raw, df_ghosts_raw = load_and_process_data(uploaded_file)
    except Exception as e:
        st.error("❌ Errore: Il file caricato non è valido.")
        st.info("💡 Assicurati di aver caricato l'esportazione grezza originale di Aircall.")
        st.stop()
    
    df_merged_raw['Giorno'] = df_merged_raw['datetime'].dt.date
    df_ghosts_raw['Giorno'] = df_ghosts_raw['datetime'].dt.date
    
    st.sidebar.divider()
    st.sidebar.subheader("📅 Filtro Temporale Globale")
    if not df_merged_raw.empty:
        min_date = df_merged_raw['Giorno'].min()
        max_date = df_merged_raw['Giorno'].max()
    else:
        min_date = pd.Timestamp.today().date()
        max_date = min_date

    # FIX DATE: Aggiunto format="DD/MM/YYYY" per forzare la visualizzazione europea
    date_filter = st.sidebar.date_input(
        "Seleziona periodo:", 
        [min_date, max_date], 
        min_value=min_date, 
        max_value=max_date,
        format="DD/MM/YYYY"
    )
    
    if len(date_filter) == 2:
        start_date, end_
