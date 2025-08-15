
import os
import datetime as dt
import pandas as pd
import streamlit as st
import altair as alt
import gspread
from google.oauth2 import service_account

st.set_page_config(page_title="Inventario de Herramientas", page_icon="🛠️", layout="wide")

SERVICE_INFO = st.secrets.get("gcp_service_account", None)
SPREADSHEET_ID = st.secrets.get("SPREADSHEET_ID", "")

if SERVICE_INFO is None or not SPREADSHEET_ID:
    st.error("Faltan secretos: gcp_service_account y/o SPREADSHEET_ID. Configúralos en Streamlit Cloud > Settings > Secrets.")
    st.stop()

SCOPES = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
creds = service_account.Credentials.from_service_account_info(SERVICE_INFO, scopes=SCOPES)
gc = gspread.authorize(creds)
sh = gc.open_by_key(SPREADSHEET_ID)

MOV_SHEET = "MOVIMIENTOS"

CAT_COLORS = {
    "HERRAMIENTA GENERAL": "#FFFFFF",
    "DESESCOMBRO": "#00A86B",
    "INICIO OBRA": "#E32636",
    "ALBAÑILERIA": "#FFD300",
    "PINTURA Y MASILLA": "#1E90FF",
    "ELECTRICIDAD": "#FF69B4",
    "PLADUR": "#A9A9A9",
    "CARPINTERIA": "#8B4513",
    "VARIOS": "#000000",
}

MASTER_COLS = ["Herramienta", "Marca", "Referencia", "Asignado", "Tipo", "CantidadInicial"]
OPTIONAL_COLS = ["Fecha"]

def get_all_category_sheets():
    sheets = []
    for ws in sh.worksheets():
        if ws.title.strip().upper() != MOV_SHEET:
            sheets.append(ws)
    return sheets

def df_from_ws(ws):
    rows = ws.get_all_records()
    df = pd.DataFrame(rows)
    df.columns = [str(c).strip() for c in df.columns]
    for c in MASTER_COLS:
        if c not in df.columns:
            df[c] = None
    for c in OPTIONAL_COLS:
        if c not in df.columns:
            df[c] = None
    df["Categoria"] = ws.title.strip().upper()
    if "CantidadInicial" in df.columns:
        df["CantidadInicial"] = pd.to_numeric(df["CantidadInicial"], errors="coerce").fillna(0).astype(int)
    for c in ["Herramienta","Marca","Referencia","Asignado","Tipo","Fecha"]:
        if c in df.columns:
            df[c] = df[c].fillna("").astype(str).str.strip()
    return df

def get_master_df():
    dfs = [df_from_ws(ws) for ws in get_all_category_sheets()]
    if not dfs:
        return pd.DataFrame(columns=MASTER_COLS + ["Categoria"] + OPTIONAL_COLS)
    return pd.concat(dfs, ignore_index=True)

def get_movements_df():
    try:
        ws = sh.worksheet(MOV_SHEET)
    except gspread.exceptions.WorksheetNotFound:
        return pd.DataFrame()
    rows = ws.get_all_records()
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    df.columns = [str(c).strip() for c in df.columns]
    if "Fecha" in df.columns:
        df["Fecha"] = pd.to_datetime(df["Fecha"], errors="coerce").dt.date
    if "Timestamp" in df.columns:
        try:
            df["Timestamp"] = pd.to_datetime(df["Timestamp"], errors="coerce")
        except Exception:
            pass
    if "Cantidad" in df.columns:
        df["Cantidad"] = pd.to_numeric(df["Cantidad"], errors="coerce").fillna(0).astype(int)
    if "Movimiento" in df.columns:
        df["Movimiento"] = df["Movimiento"].astype(str).str.lower().str.strip()
    return df

def ensure_movements_sheet_exists():
    try:
        sh.worksheet(MOV_SHEET)
        return
    except gspread.exceptions.WorksheetNotFound:
        ws = sh.add_worksheet(title=MOV_SHEET, rows=1000, cols=20)
        headers = ["Timestamp","Fecha","Categoria","Herramienta","Referencia","Marca","Asignado","Tipo","Movimiento","Cantidad","Observaciones"]
        ws.append_row(headers, value_input_option="USER_ENTERED")

def append_movement(row_dict):
    ensure_movements_sheet_exists()
    ws = sh.worksheet(MOV_SHEET)
    headers = ws.row_values(1)
    values = [row_dict.get(h, "") for h in headers]
    ws.append_row(values, value_input_option="USER_ENTERED")

def update_master_row(category_title, key_values, updates):
    ws = sh.worksheet(category_title)
    data = ws.get_all_values()
    if not data:
        raise ValueError("Hoja vacía o sin cabecera.")
    headers = [h.strip() for h in data[0]]
    col_idx = {h: i for i, h in enumerate(headers)}
    for uc in updates.keys():
        if uc not in col_idx:
            headers.append(uc)
            col_idx[uc] = len(headers) - 1
            ws.update_cell(1, col_idx[uc]+1, uc)
            data = ws.get_all_values()
            if not data:
                data = [headers]
    key_h = key_values.get("Herramienta", "")
    key_r = key_values.get("Referencia", "")
    target_row = None
    for i in range(1, len(data)):
        row = data[i]
        row = row + [""] * (len(headers)-len(row))
        hval = row[col_idx.get("Herramienta", -1)] if "Herramienta" in col_idx else ""
        rval = row[col_idx.get("Referencia", -1)] if "Referencia" in col_idx else ""
        if str(hval).strip() == str(key_h).strip() and str(rval).strip() == str(key_r).strip():
            target_row = i + 1
            break
    if target_row is None:
        raise ValueError("No se encontró la fila a actualizar (revisa Herramienta/Referencia).")
    for uc, v in updates.items():
        ws.update_cell(target_row, col_idx[uc]+1, v)

def compute_stock(df_master, df_mov):
    if df_master.empty:
        return df_master.assign(StockActual=0)
    if df_mov is None or df_mov.empty:
        out = df_master.copy()
        out["StockActual"] = out.get("CantidadInicial", 0)
        return out
    dfm = df_mov.copy()
    dfm["signo"] = dfm["Movimiento"].map({"entrada":1, "salida":-1}).fillna(0)
    dfm["ajuste"] = dfm["signo"] * dfm["Cantidad"].fillna(0)
    key_cols = ["Categoria","Herramienta","Referencia"]
    for c in key_cols:
        if c not in dfm.columns:
            dfm[c] = ""
    movsum = dfm.groupby(key_cols, as_index=False)["ajuste"].sum()
    base = df_master.copy()
    if "CantidadInicial" not in base.columns:
        base["CantidadInicial"] = 0
    merged = base.merge(movsum, on=key_cols, how="left")
    merged["ajuste"] = merged["ajuste"].fillna(0)
    merged["StockActual"] = merged["CantidadInicial"].fillna(0) + merged["ajuste"]
    return merged

with st.sidebar:
    st.header("⚙️ Opciones")
    if st.button("🔄 Refrescar datos"):
        st.rerun()
    st.divider()
    st.caption("Colores por categoría")
    for cat, col in CAT_COLORS.items():
        st.markdown(f"<div style='display:flex;align-items:center;gap:8px;'>"
                    f"<div style='width:14px;height:14px;background:{col};border:1px solid #ccc;'></div>"
                    f"<span>{cat.title()}</span></div>", unsafe_allow_html=True)

df_master = get_master_df()
df_mov = get_movements_df()
df_stock = compute_stock(df_master, df_mov)

st.title("🛠️ Inventario de Herramientas — Formulario Web")
st.caption("Conectado a Google Sheets. Cambios desde aquí y desde la hoja se sincronizan.")

st.subheader("Selecciona herramienta")
lista = (
    df_master[["Categoria","Herramienta","Referencia"]]
    .fillna("")
    .drop_duplicates()
    .assign(Display=lambda d: d["Herramienta"].astype(str) + " — " + d["Referencia"].replace("", "(sin ref.)"))
    .sort_values("Herramienta")
)
sel = st.selectbox("Herramienta (orden alfabético)", options=list(lista["Display"]), index=None, placeholder="Elige una herramienta…")

if sel:
    row_sel = lista[lista["Display"] == sel].iloc[0]
    cat_sel = str(row_sel["Categoria"]).strip().upper()
    herra_sel = str(row_sel["Herramienta"]).strip()
    ref_sel = str(row_sel["Referencia"]).strip()
    ficha = df_master[(df_master["Categoria"]==cat_sel) & (df_master["Herramienta"]==herra_sel) & (df_master["Referencia"]==ref_sel)].head(1).squeeze()

    color = CAT_COLORS.get(cat_sel, "#EEEEEE")
    st.markdown(f"<div style='padding:10px;border-radius:12px;border:1px solid #ddd;"
                f"background:linear-gradient(90deg, {color}33, #ffffff);'>"
                f"<b>Categoría:</b> <span style='color:{color};font-weight:700'>{cat_sel.title()}</span></div>",
                unsafe_allow_html=True)

    col1, col2, col3, col4, col5, col6 = st.columns(6)
    with col1:
        st.text_input("Marca", value=str(ficha.get("Marca","")), key="marca_val")
    with col2:
        st.text_input("Referencia", value=str(ficha.get("Referencia","")), key="ref_val")
    with col3:
        st.text_input("Asignado a", value=str(ficha.get("Asignado","")), key="asignado_val")
    with col4:
        st.text_input("Tipo de herramienta", value=str(ficha.get("Tipo","")), key="tipo_val")
    with col5:
        st.number_input("Cantidad inicial (solo lectura)", value=float(ficha.get("CantidadInicial",0) or 0), disabled=True)
    with col6:
        st.text_input("Fecha (si existe en tu hoja)", value=str(ficha.get("Fecha","") or ""), key="fecha_val")

    stock_row = df_stock[(df_stock["Categoria"]==cat_sel) & (df_stock["Herramienta"]==herra_sel) & (df_stock["Referencia"]==ref_sel)]
    stock_actual = int(stock_row["StockActual"].iloc[0]) if not stock_row.empty else int(ficha.get("CantidadInicial") or 0)
    st.info(f"📦 Stock actual calculado: **{stock_actual}**")

    st.subheader("Registrar movimiento (Entrada / Salida)")
    colm1, colm2, colm3 = st.columns([1,1,2])
    with colm1:
        tipo_mov = st.selectbox("Movimiento", options=["Entrada","Salida"])
    with colm2:
        cantidad = st.number_input("Cantidad", min_value=1, step=1, value=1)
    with colm3:
        fecha_mov = st.date_input("Fecha", value=dt.date.today())
    obs = st.text_area("Observaciones (opcional)", placeholder="Ej: devuelta por Juan, rota, etc.")

    if st.button("💾 Guardar movimiento"):
        fila = {
            "Timestamp": dt.datetime.now().isoformat(),
            "Fecha": fecha_mov.isoformat(),
            "Categoria": cat_sel,
            "Herramienta": herra_sel,
            "Referencia": st.session_state.get("ref_val",""),
            "Marca": st.session_state.get("marca_val",""),
            "Asignado": st.session_state.get("asignado_val",""),
            "Tipo": st.session_state.get("tipo_val",""),
            "Movimiento": tipo_mov.lower(),
            "Cantidad": int(cantidad),
            "Observaciones": obs,
        }
        try:
            append_movement(fila)
            st.success("Movimiento guardado en 'MOVIMIENTOS'.")
        except Exception as e:
            st.error(f"No se pudo guardar el movimiento: {e}")

    st.divider()
    st.subheader("Editar ficha (hoja maestra)")
    st.caption("Actualiza columnas en la hoja de su categoría sin modificar CantidadInicial.")
    if st.button("✏️ Guardar cambios en el master"):
        try:
            updates = {
                "Marca": st.session_state.get("marca_val",""),
                "Referencia": st.session_state.get("ref_val",""),
                "Asignado": st.session_state.get("asignado_val",""),
                "Tipo": st.session_state.get("tipo_val",""),
                "Fecha": st.session_state.get("fecha_val",""),
            }
            key_values = {"Herramienta": herra_sel, "Referencia": ref_sel}
            update_master_row(cat_sel, key_values, updates)
            st.success("Ficha actualizada en la hoja de categoría.")
        except Exception as e:
            st.error(f"No se pudo actualizar la ficha: {e}")
else:
    st.info("Elige una herramienta para ver/editar sus datos y registrar movimientos.")

st.header("📊 Reportes")
if not df_stock.empty:
    agg = df_stock.groupby("Categoria", as_index=False)["StockActual"].sum()
    color_scale = alt.Scale(domain=list(CAT_COLORS.keys()), range=list(CAT_COLORS.values()))
    chart1 = alt.Chart(agg).mark_bar().encode(
        x=alt.X("Categoria:N", sort="-y", title="Categoría"),
        y=alt.Y("StockActual:Q", title="Stock actual"),
        color=alt.Color("Categoria:N", scale=color_scale, legend=None),
        tooltip=["Categoria","StockActual"]
    ).properties(height=300)
    st.altair_chart(chart1, use_container_width=True)
else:
    st.write("No hay datos de stock para graficar.")

dfm = get_movements_df()
if dfm is not None and not dfm.empty:
    if "Fecha" in dfm.columns:
        dfm["Fecha"] = pd.to_datetime(dfm["Fecha"], errors="coerce").dt.date
    else:
        dfm["Fecha"] = pd.to_datetime(dfm.get("Timestamp"), errors="coerce").dt.date
    dfm = dfm.dropna(subset=["Fecha"])
    entradas = dfm[dfm["Movimiento"]=="entrada"].groupby("Fecha", as_index=False)["Cantidad"].sum().rename(columns={"Cantidad":"Entradas"})
    salidas = dfm[dfm["Movimiento"]=="salida"].groupby("Fecha", as_index=False)["Cantidad"].sum().rename(columns={"Cantidad":"Salidas"})
    merged = pd.merge(entradas, salidas, on="Fecha", how="outer").fillna(0).sort_values("Fecha")
    merged_melt = merged.melt("Fecha", var_name="Tipo", value_name="Cantidad")
    chart2 = alt.Chart(merged_melt).mark_line(point=True).encode(
        x=alt.X("Fecha:T", title="Fecha"),
        y=alt.Y("Cantidad:Q"),
        color="Tipo:N",
        tooltip=["Fecha:T","Tipo","Cantidad:Q"]
    ).properties(height=300)
    st.altair_chart(chart2, use_container_width=True)
else:
    st.write("No hay movimientos para graficar.")

if dfm is not None and not dfm.empty:
    dfm["AbsCantidad"] = dfm["Cantidad"].abs()
    top = (dfm.groupby(["Herramienta","Referencia"], as_index=False)["AbsCantidad"].sum()
              .sort_values("AbsCantidad", ascending=False).head(10))
    chart3 = alt.Chart(top).mark_bar().encode(
        x=alt.X("AbsCantidad:Q", title="Volumen movido total"),
        y=alt.Y("Herramienta:N", sort="-x", title="Herramienta"),
        tooltip=["Herramienta","Referencia","AbsCantidad"]
    ).properties(height=300)
    st.altair_chart(chart3, use_container_width=True)
else:
    st.write("No hay suficientes movimientos para el Top 10.")
