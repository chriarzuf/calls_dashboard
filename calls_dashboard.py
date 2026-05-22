import streamlit as st
import pandas as pd
import numpy as np
import holidays
from pandas.tseries.offsets import CustomBusinessDay

# ==========================================
# CONFIGURAZIONE PAGINA
# ==========================================
st.set_page_config(page_title="Dashboard SLA Aircall v5", layout="wide")
st.title("📊 Dashboard Analisi SLA Inbound - Aircall")

# ==========================================
# CALENDARIO FESTIVI ITALIANI AUTOMATICO
# ==========================================
anni_interesse = [2024, 2025, 2026, 2027]
festivi_it = holidays.IT(years=anni_interesse, prov='MI')
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
    
    df['datetime'] = pd.to_datetime(df['datetime (tz offset incl.)'])
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
        esito = 'Verde' if delta_seconds <= 3600 else 'Rosso'
    else:
        if not is_business_day or call_time.hour >= 18:
            next_bday = pd.Timestamp(call_time.date()) + it_bday
        else: 
            next_bday = pd.Timestamp(call_time.date())
            
        deadline = next_bday.replace(hour=10, minute=0, second=0)
        esito = 'Verde' if resolve_time <= deadline else 'Rosso'
        
    advisor_assegnato = row['advisor_risoluzione'] if esito == 'Verde' else 'In Ritardo'
    return esito, advisor_assegnato

# ==========================================
# 3. FILTRO GLOBALE E APPLICAZIONE REGOLE
# ==========================================
if uploaded_file is not None:
    df_merged_raw, df_ghosts_raw = load_and_process_data(uploaded_file)
    
    df_merged_raw['Giorno'] = df_merged_raw['datetime'].dt.date
    df_ghosts_raw['Giorno'] = df_ghosts_raw['datetime'].dt.date
    
    # --- FILTRO GLOBALE NELLA SIDEBAR ---
    st.sidebar.divider()
    st.sidebar.subheader("📅 Filtro Temporale Globale")
    if not df_merged_raw.empty:
        min_date = df_merged_raw['Giorno'].min()
        max_date = df_merged_raw['Giorno'].max()
    else:
        min_date = pd.Timestamp.today().date()
        max_date = min_date

    date_filter = st.sidebar.date_input("Seleziona periodo:", [min_date, max_date], min_value=min_date, max_value=max_date)
    
    if len(date_filter) == 2:
        start_date, end_date = date_filter
    elif len(date_filter) == 1:
        start_date = end_date = date_filter[0]
    else:
        start_date, end_date = min_date, max_date
        
    df_merged = df_merged_raw[(df_merged_raw['Giorno'] >= start_date) & (df_merged_raw['Giorno'] <= end_date)].copy()
    df_ghosts = df_ghosts_raw[(df_ghosts_raw['Giorno'] >= start_date) & (df_ghosts_raw['Giorno'] <= end_date)].copy()
    
    if not df_merged.empty:
        df_merged[['SLA', 'Advisor_Competente']] = df_merged.apply(applica_regole_sla, axis=1, result_type='expand')
    else:
        df_merged['SLA'] = pd.Series(dtype='str')
        df_merged['Advisor_Competente'] = pd.Series(dtype='str')
        
    mappa_giorni = {'Monday':'Lunedì', 'Tuesday':'Martedì', 'Wednesday':'Mercoledì', 'Thursday':'Giovedì', 'Friday':'Venerdì', 'Saturday':'Sabato', 'Sunday':'Domenica'}
    df_merged['Giorno_Settimana'] = df_merged['datetime'].dt.day_name().map(mappa_giorni)
    
    df_merged['hour_float'] = df_merged['datetime'].dt.hour + (df_merged['datetime'].dt.minute / 60.0)
    df_merged['Fascia_Oraria'] = pd.cut(
        df_merged['hour_float'], 
        bins=orari_fasce_float, 
        labels=etichette_fasce,
        right=False
    ).astype(str)
    
    df_fasce = df_merged[df_merged['Fascia_Oraria'] != 'nan'].copy()

    # --- EXPORT DATI ---
    st.sidebar.divider()
    st.sidebar.subheader("📥 Esporta Dati Filtrati")
    csv_data = df_merged.to_csv(index=False).encode('utf-8')
    st.sidebar.download_button(
        label="Scarica Dataset (CSV)",
        data=csv_data,
        file_name="aircall_sla_elaborati_filtrati.csv",
        mime="text/csv",
    )

    # ---------------------------------------------------------
    # CREAZIONE DELLE PAGINE LOGICHE
    # ---------------------------------------------------------
    tab1, tab2, tab3 = st.tabs([
        "📈 1. Overview & Trend", 
        "👤 2. Analisi Turni", 
        "👻 3. Hub Ghost Calls"
    ])
    
    # --- TAB 1: OVERVIEW GLOBALE ---
    with tab1:
        st.subheader("Indicatori di Performance (Inbound Reali)")
        tot_calls = len(df_merged)
        sla_verde = len(df_merged[df_merged['SLA'] == 'Verde'])
        tasso_sla = (sla_verde / tot_calls) * 100 if tot_calls > 0 else 0
        
        col1, col2, col3 = st.columns(3)
        col1.metric("Totale Inbound Effettive", tot_calls)
        col2.metric("In SLA (Verde)", sla_verde)
        if tasso_sla >= 90:
            col3.metric("Tasso Rispetto SLA", f"{tasso_sla:.1f}%", delta="🎯 Target Raggiunto")
        else:
            col3.metric("Tasso Rispetto SLA", f"{tasso_sla:.1f}%", delta=f"-{90-tasso_sla:.1f}% sotto target", delta_color="inverse")
            
        st.divider()
        
        st.write("**Andamento Storico Giornaliero**")
        if not df_merged.empty:
            pivot_giorno = df_merged.groupby(['Giorno', 'SLA']).size().unstack(fill_value=0).reset_index()
            for c in ['Verde', 'Rosso']: 
                if c not in pivot_giorno.columns: pivot_giorno[c] = 0
            pivot_giorno['Totale'] = pivot_giorno['Verde'] + pivot_giorno['Rosso']
            pivot_giorno['% Verde'] = (pivot_giorno['Verde'] / pivot_giorno['Totale']) * 100
            
            # Adesso il grafico prende automaticamente l'intera larghezza del layout
            st.bar_chart(pivot_giorno.set_index('Giorno')[['Verde', 'Rosso']], color=["#28a745", "#ff4b4b"])
        else:
            st.warning("Nessun dato nel periodo selezionato.")

        st.divider()
        
        st.write("**🔴 Heatmap Inefficienze: Concentrazione Chiamate SLA Rosso**")
        df_rossi = df_fasce[df_fasce['SLA'] == 'Rosso']
        
        if not df_rossi.empty:
            heatmap_data = df_rossi.pivot_table(
                index='Giorno_Settimana', 
                columns='Fascia_Oraria', 
                aggfunc='size', 
                fill_value=0
            )
            ordine_giorni = ['Lunedì', 'Martedì', 'Mercoledì', 'Giovedì', 'Venerdì', 'Sabato', 'Domenica']
            heatmap_data = heatmap_data.reindex(ordine_giorni).dropna(how='all')
            
            st.dataframe(
                heatmap_data.style
                .format("{:.0f}")
                .background_gradient(cmap='Reds', axis=None), 
                use_container_width=True
            )
        else:
            st.success("Nessuno SLA Rosso rilevato nel periodo per generare la Heatmap.")

    # --- TAB 2: MATRICE E SCORECARD ---
    with tab2:
        st.subheader("🎯 Scorecard di Turno (Sintesi per Fascia Oraria)")
        st.write("Valuta l'efficienza della fascia oraria indipendentemente dall'Advisor. I colori evidenziano le criticità nel periodo selezionato.")
        
        if not df_fasce.empty:
            scorecard = df_fasce.groupby('Fascia_Oraria').agg(
                Totale_Inbound=('datetime', 'count'),
                SLA_Verde=('SLA', lambda x: (x == 'Verde').sum()),
                SLA_Rosso=('SLA', lambda x: (x == 'Rosso').sum())
            ).reset_index()
            scorecard['% SLA Verde'] = (scorecard['SLA_Verde'] / scorecard['Totale_Inbound'] * 100).fillna(0)
            
            st.dataframe(
                scorecard.style
                .format({'% SLA Verde': '{:.0f}%'})
                .background_gradient(subset=['% SLA Verde'], cmap='Reds_r', vmin=0, vmax=100),
                use_container_width=True
            )
        else:
            st.warning("Nessun dato disponibile per calcolare la scorecard nel periodo.")
        
        st.divider()

        st.subheader("👤 Analisi Operativa: Incroci Fascia e Advisor")
        col_f1, col_f2 = st.columns(2)
        with col_f1:
            fasce_pulite = [str(x) for x in df_fasce['Fascia_Oraria'].unique() if pd.notna(x) and str(x) != 'nan']
            elenco_fasce = sorted(fasce_pulite)
            filtro_fasce = st.multiselect("Filtra Fascia Oraria:", options=elenco_fasce, default=elenco_fasce)
            
        with col_f2:
            advisors_puliti = [str(x) for x in df_fasce['Advisor_Competente'].unique() if pd.notna(x) and str(x) != 'nan']
            elenco_advisors = sorted(advisors_puliti)
            filtro_advisors = st.multiselect("Filtra Advisor / Stato Gestione:", options=elenco_advisors, default=elenco_advisors)
            
        df_filtrato = df_fasce[
            (df_fasce['Fascia_Oraria'].isin(filtro_fasce)) & 
            (df_fasce['Advisor_Competente'].isin(filtro_advisors))
        ]
        
        if not df_filtrato.empty:
            pivot_adv = df_filtrato.pivot_table(
                index=['Fascia_Oraria', 'Advisor_Competente'],
                columns='SLA',
                aggfunc='size',
                fill_value=0
            ).reset_index()
            
            for c in ['Verde', 'Rosso']: 
                if c not in pivot_adv.columns: pivot_adv[c] = 0
                
            pivot_adv['Totale'] = pivot_adv['Verde'] + pivot_adv['Rosso']
            pivot_adv['% SLA Verde'] = (pivot_adv['Verde'] / pivot_adv['Totale'] * 100).fillna(0)
            st.dataframe(pivot_adv.style.format({'% SLA Verde': '{:.1f}%'}), use_container_width=True)
        else:
            st.warning("Nessun dato corrispondente ai filtri selezionati.")

    # --- TAB 3: GHOST CALLS ---
    with tab3:
        st.subheader("Analisi Ghost Calls (Escluse dallo SLA)")
        tot_ghosts = len(df_ghosts)
        tot_inbound_grezze = len(df_merged) + tot_ghosts
        incidenza_ghost = (tot_ghosts / tot_inbound_grezze * 100) if tot_inbound_grezze > 0 else 0
        
        col_g1, col_g2 = st.columns(2)
        col_g1.metric("Ghost Calls Totali", tot_ghosts)
        col_g2.metric("Incidenza su Totale Inbound", f"{incidenza_ghost:.1f}%")
        
        if tot_ghosts > 0:
            df_ghosts['Ora'] = df_ghosts['datetime'].dt.hour
            df_ghosts['Giorno_F'] = df_ghosts['datetime'].dt.date
            
            col_ch1, col_ch2 = st.columns(2)
            with col_ch1:
                st.write("**Ghost Calls per Giorno**")
                ghost_giorno = df_ghosts.groupby('Giorno_F').size().reset_index(name='Volume')
                st.dataframe(ghost_giorno.set_index('Giorno_F'), use_container_width=True)
            with col_ch2:
                st.write("**Distribuzione Oraria**")
                ghost_ora = df_ghosts.groupby('Ora').size().reset_index(name='Volume')
                ore_complete = pd.DataFrame({'Ora': range(0, 24)})
                ghost_ora_completo = pd.merge(ore_complete, ghost_ora, on='Ora', how='left').fillna(0)
                st.bar_chart(ghost_ora_completo.set_index('Ora')['Volume'])
                
            st.write("**Registro Ispezione Analitico Ghost Calls**")
            cols_to_show = ['datetime', 'customer_number', 'waiting_seconds', 'missed_call_reason']
            cols_available = [c for c in cols_to_show if c in df_ghosts.columns]
            
            st.dataframe(
                df_ghosts[cols_available].rename(
                    columns={'datetime': 'Data/Ora', 'customer_number': 'Num. Cliente', 'waiting_seconds': 'Attesa (s)', 'missed_call_reason': 'Ragione'}
                ), 
                use_container_width=True
            )
        else:
            st.success("Nessuna Ghost Call individuata nel periodo selezionato.")

else:
    st.info("ℹ️ Carica il file di esportazione delle chiamate di Aircall per sbloccare i pannelli di analisi.")
