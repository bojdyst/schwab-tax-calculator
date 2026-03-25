import streamlit as st
import pandas as pd
import json
import requests
from datetime import datetime, timedelta

def parse_money(money_str):
    """Zamienia string w formacie '$115.01' lub '-$1,209.80' na float."""
    if not isinstance(money_str, str):
        return 0.0
    clean_str = money_str.replace('$', '').replace(',', '')
    try:
        return float(clean_str)
    except ValueError:
        return 0.0

def get_previous_working_day(date_obj):
    """Zwraca poprzedni dzień roboczy (ignoruje weekendy)."""
    prev_day = date_obj - timedelta(days=1)
    while prev_day.weekday() > 4:  # 5 to sobota, 6 to niedziela
        prev_day -= timedelta(days=1)
    return prev_day

@st.cache_data
def get_nbp_rate(currency, date_str):
    """
    Pobiera kurs średni NBP z ostatniego dnia roboczego poprzedzającego podaną datę.
    date_str musi być w formacie 'MM/DD/YYYY'.
    """
    try:
        date_obj = datetime.strptime(date_str, "%m/%d/%Y")
    except ValueError:
        return None, None

    target_date = get_previous_working_day(date_obj)

    # Próbujemy pobrać kurs, cofając się w razie świąt (np. Boże Ciało, 1 Maja)
    for _ in range(10):
        formatted_date = target_date.strftime("%Y-%m-%d")
        url = f"http://api.nbp.pl/api/exchangerates/rates/A/{currency}/{formatted_date}/?format=json"
        response = requests.get(url)

        if response.status_code == 200:
            data = response.json()
            return data['rates'][0]['mid'], formatted_date

        target_date -= timedelta(days=1)

    return None, None

def process_transactions(transactions):
    """Przetwarza listę transakcji i wylicza podatki zgodnie z T-1 NBP."""
    results = []

    for t in transactions:
        # Interesują nas tylko sprzedaże akcji
        if t.get("Action") != "Sale":
            continue

        sale_date_str = t.get("Date")
        # Wyciągamy rok podatkowy z daty sprzedaży
        try:
            sale_year = datetime.strptime(sale_date_str, "%m/%d/%Y").year
        except ValueError:
            sale_year = "Nieznany"

        for detail_wrapper in t.get("TransactionDetails", []):
            details = detail_wrapper.get("Details", {})

            shares = float(details.get("Shares", 0))
            sale_price_usd = parse_money(details.get("SalePrice"))
            purchase_price_usd = parse_money(details.get("PurchasePrice"))
            purchase_date_str = details.get("PurchaseDate")

            # 1. Obliczenia w USD
            revenue_usd = shares * sale_price_usd
            cost_usd = shares * purchase_price_usd

            # 2. Kursy NBP (T-1)
            sale_rate, sale_rate_date = get_nbp_rate("USD", sale_date_str)
            purchase_rate, purchase_rate_date = get_nbp_rate("USD", purchase_date_str)

            # Jeśli nie udało się pobrać kursu (np. zły format daty), pomijamy lub ustawiamy zera
            if not sale_rate or not purchase_rate:
                continue

            # 3. Przeliczenie na PLN
            revenue_pln = revenue_usd * sale_rate
            cost_pln = cost_usd * purchase_rate
            income_pln = revenue_pln - cost_pln
            tax_pln = income_pln * 0.19 if income_pln > 0 else 0.0

            results.append({
                "Rok podatkowy": sale_year,
                "Data sprzedaży": sale_date_str,
                "Ilość akcji": shares,
                "Cena sprzedaży (USD)": sale_price_usd,
                "Przychód (USD)": revenue_usd,
                "Kurs NBP (Sprzedaż)": f"{sale_rate:.4f} z dn. {sale_rate_date}",
                "Przychód (PLN)": revenue_pln,
                "Data zakupu": purchase_date_str,
                "Cena zakupu (USD)": purchase_price_usd,
                "Koszt (USD)": cost_usd,
                "Kurs NBP (Zakup)": f"{purchase_rate:.4f} z dn. {purchase_rate_date}",
                "Koszt (PLN)": cost_pln,
                "Dochód/Strata (PLN)": income_pln,
                "Podatek 19% (PLN)": tax_pln
            })

    return pd.DataFrame(results)

# --- INTERFEJS STREAMLIT ---
st.set_page_config(page_title="Kalkulator Podatku Belki", layout="wide")

st.title("📊 Kalkulator Zysków Kapitałowych (Akcje zagraniczne)")
st.markdown("""
Aplikacja oblicza podatek od zysków kapitałowych (19%) dla transakcji w USD.
Koszty i przychody przeliczane są po średnim kursie NBP z **ostatniego dnia roboczego poprzedzającego dzień transakcji (T-1)**.
""")

uploaded_file = st.file_uploader("Wgraj plik JSON z historią transakcji", type="json")

if uploaded_file is not None:
    data = json.load(uploaded_file)
    transactions = data.get("Transactions", [])

    st.info(f"Wczytano plik. Zakres dat w pliku: {data.get('FromDate')} - {data.get('ToDate')}")

    with st.spinner("Pobieram historyczne kursy NBP i przeliczam dane (to może chwilę potrwać)..."):
        df = process_transactions(transactions)

    if not df.empty:
        # Pobieramy unikalne lata podatkowe i sortujemy je rosnąco
        lata_podatkowe = sorted(df['Rok podatkowy'].unique())

        st.header("Podsumowanie do PIT-38 z podziałem na lata")

        # Tworzymy zakładki (tabs) dla każdego roku podatkowego
        tabs = st.tabs([f"Rok {rok}" for rok in lata_podatkowe])

        for i, rok in enumerate(lata_podatkowe):
            with tabs[i]:
                # Filtrujemy dane tylko dla konkretnego roku
                df_rok = df[df['Rok podatkowy'] == rok]

                total_revenue = df_rok["Przychód (PLN)"].sum()
                total_cost = df_rok["Koszt (PLN)"].sum()
                total_income = df_rok["Dochód/Strata (PLN)"].sum()
                total_tax = df_rok["Podatek 19% (PLN)"].sum()

                # Wyświetlamy główne metryki dla danego roku
                col1, col2, col3, col4 = st.columns(4)
                col1.metric("Łączny Przychód (PLN)", f"{total_revenue:.2f} zł")
                col2.metric("Łączne Koszty (PLN)", f"{total_cost:.2f} zł")
                col3.metric("Całkowity Dochód (PLN)", f"{total_income:.2f} zł")
                col4.metric("Podatek do zapłaty (19%)", f"{total_tax:.2f} zł")

                st.divider()
                st.subheader(f"Szczegóły transakcji sprzedaży w {rok} roku")

                # Formatowanie i wyświetlanie tabeli (bez kolumny 'Rok podatkowy', bo jest w tytule zakładki)
                st.dataframe(df_rok.drop(columns=['Rok podatkowy']).style.format({
                    "Przychód (USD)": "${:.2f}",
                    "Cena sprzedaży (USD)": "${:.2f}",
                    "Przychód (PLN)": "{:.2f} zł",
                    "Cena zakupu (USD)": "${:.2f}",
                    "Koszt (USD)": "${:.2f}",
                    "Koszt (PLN)": "{:.2f} zł",
                    "Dochód/Strata (PLN)": "{:.2f} zł",
                    "Podatek 19% (PLN)": "{:.2f} zł"
                }), use_container_width=True)

        with st.expander("Metodologia obliczeń (Jak to zostało policzone?)"):
            st.markdown("""
            ### Zasady polskiego prawa podatkowego zastosowane w kalkulatorze:
            1. **Filtrowanie:** Aplikacja ignoruje operacje typu "Wire Transfer" (wypłata gotówki na konto nie rodzi obowiązku podatkowego z tytułu zysków kapitałowych). Skupia się wyłącznie na akcjach oznaczonych jako "Sale".
            2. **Przychód:** Ilość sprzedanych akcji * Cena Sprzedaży (SalePrice).
            3. **Koszty uzyskania przychodu:** Ilość sprzedanych akcji * Cena Zakupu (PurchasePrice).
            4. **Przewalutowanie Przychodu:** Zastosowano średni kurs NBP z ostatniego dnia roboczego przed **datą sprzedaży**.
            5. **Przewalutowanie Kosztów:** Zastosowano średni kurs NBP z ostatniego dnia roboczego przed **datą zakupu**.
            6. **Dochód:** Przychód w PLN - Koszty w PLN. Sumowany w ramach danego roku kalendarzowego.
            7. **Podatek:** 19% od wyliczonego dochodu (jeśli w danym roku wystąpiła łączna strata, podatek wynosi 0 zł).

            *Uwaga dotycząca ESPP: Kalkulator liczy klasyczny podatek giełdowy (od zysków kapitałowych) na potrzeby PIT-38. Upewnij się, czy zniżka (discount) uzyskana przy zakupie akcji ESPP nie podlega u Ciebie pod opodatkowanie jako przychód ze stosunku pracy na dokumencie PIT-36, co jest osobną kwestią prawną.*
            """)
    else:
        st.warning("Nie znaleziono żadnych poprawnych transakcji typu 'Sale' w wgranym pliku JSON.")