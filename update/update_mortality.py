#!/usr/bin/env python3
# coding: utf-8

import io
import json
import uuid
import time
import py7zr
import base64
import chardet
import requests
import warnings
import urllib3
import datetime
import unidecode
import traceback
import itertools

import perkins
import perkins.requests
import perkins.input.powerbi

from bs4 import BeautifulSoup
from zipfile import ZipFile

import pandas as pd
import numpy as np

warnings.filterwarnings('ignore', category=urllib3.exceptions.InsecureRequestWarning)
warnings.filterwarnings('ignore', category=FutureWarning)
warnings.filterwarnings('ignore', category=UserWarning)
warnings.filterwarnings('ignore', category=pd.errors.PerformanceWarning)


def get_iso3166(adm1_df, iso):
    global iso_geo_names, geo_names

    adm1_index = map(lambda _: unidecode.unidecode(_.lower()), adm1_df)
    adm1_index = list(adm1_index)

    country_geo_names = geo_names[geo_names['geocode'].str.startswith(iso)]
    country_geo_names = country_geo_names[
        ~country_geo_names.index.duplicated(keep='first')
    ]

    adm1_index = country_geo_names.loc[adm1_index]

    adm1_index['name'] = iso_geo_names.loc[
        adm1_index['geocode'].values
    ][0].values
    adm1_index.index = adm1_df

    return adm1_index


def get_population():
    geo_sa_df = pd.read_csv('./update/south.america.population.csv')
    geo_sa_df_ = geo_sa_df.groupby([
        'name_0', 'name_1', 'name_2'
    ])['population'].sum()

    # All adm level 2 with population over 100k or biggest for each adm level 1
    sa_cities = pd.DataFrame([
        *geo_sa_df_[geo_sa_df_ > 1e5].index.to_list(),
        *geo_sa_df_.groupby(level=['name_0', 'name_1']).idxmax().values
    ]).drop_duplicates()

    sa_cities.columns = ['name_0', 'name_1', 'name_2']
    sa_cities = sa_cities.sort_values(['name_0', 'name_1', 'name_2'])

    geo_sa_df = geo_sa_df.set_index(['name_0', 'name_1', 'name_2', 'name_3'])

    return geo_sa_df, sa_cities


ARTICLES = ['de', 'del', 'los', 'las']
def do_title(title):
    try:
        title = title.encode('cp1252').decode('utf-8')
    except:
        pass

    title = title.lower().capitalize().split(' ')
    title = title[:1] + [
        _.capitalize() if _ not in ARTICLES else _ for _ in title[1:]
    ]

    return ' '.join(title)


DF_ADM1_COLS = [
    'iso_code', 'country_name', 'adm1_isocode',
    'adm1_name', 'frequency', 'date', 'deaths'
]
DF_ADM2_COLS = [
    'iso_code', 'country_name', 'adm1_isocode',
    'adm1_name', 'adm2_name', 'frequency',
    'date', 'deaths'
]

def storage_format(df, iso_code=None, **kwargs):
    df = df.reset_index()
    df['iso_code'] = iso_code

    for k, v in kwargs.items():
        df[k] = v

    adm1_df = df['adm1_name'].unique()
    adm1_df = get_iso3166(adm1_df, iso_code)

    df['adm1_isocode'] = df['adm1_name']
    df['adm1_isocode'] = df['adm1_isocode'].map(
        adm1_df['geocode'].to_dict()
    )

    df['adm1_name'] = df['adm1_name'].map(
        adm1_df['name'].to_dict()
    )

    df['deaths'] = df['deaths'].astype(int)

    return df


CHILE_BASE_URL = 'https://deis.minsal.cl/deisajax'
CHILE_INDEX_PARAMS = {
    'action': 'wp_ajax_ninja_tables_public_action',
    'table_id': '2889',
    'target_action': 'get-all-data',
    'default_sorting': 'manual_sort',
}
CHL_ADM1_MAP = {
    'Metropolitana de Santiago': 'Santiago Metropolitan',
    'La Araucanía': 'Araucania',
    "Libertador B. O'Higgins": "O'Higgins",
    'Magallanes y de La Antártica Chilena': 'Magallanes',
    'Aisén del Gral. C. Ibáñez del Campo': 'Aisen'
}
def update_chile():
    req = requests.get(
        CHILE_BASE_URL,
        params=CHILE_INDEX_PARAMS,
        headers=perkins.DEFAULT_HEADERS
    )
    def_file = next(
        _['value'] for _ in req.json() if (
            (_['value']['tags'] == 'defunciones') and
            ('semanal' in _['value']['nombre'].lower())
        )
    )

    req = perkins.requests.do_request(def_file['ver'], max_retry=10)
    fzip = ZipFile(io.BytesIO(req.content))

    # Process metadata
    meta_file = next(
        (_ for _ in fzip.namelist() if _.endswith('xlsx')),
        None
    )
    meta_file = fzip.open(meta_file)

    meta_df = pd.read_excel(meta_file, header=None, index_col=None)
    meta_df = meta_df.iloc[3:, 1:].T.set_index(keys=3).T
    meta_df.columns = [
        unidecode.unidecode(_.lower()).replace(' ', '_') for _ in meta_df.columns
    ]

    # Process def file
    data_file = next(
        (_ for _ in fzip.namelist() if _.endswith('csv')),
        None
    )
    data_file = fzip.open(data_file)

    data_sample = data_file.read(4096)
    data_encoding = chardet.detect(data_sample)
    data_file.seek(0)

    chile_df = pd.read_csv(
        data_file,
        sep=';',
        encoding=data_encoding['encoding'],
        header=None,
        index_col=None
    )

    chile_df.columns = meta_df['nombre_de_la_variable'].str.lower().values
    if chile_df.iloc[0, 0].lower() == 'AÑO'.lower():
        chile_df = chile_df.iloc[1:]

    chile_df['fecha_def'] = pd.to_datetime(chile_df['fecha_def'])

    chile_df = chile_df.sort_values('fecha_def')
    chile_df['glosa_reg_res'] = chile_df['glosa_reg_res'].str.replace(
        r'^Del* +', '', regex=True
    )

    chile_df['glosa_reg_res'] = chile_df['glosa_reg_res'].replace(CHL_ADM1_MAP)
    chile_df = chile_df[chile_df['glosa_reg_res'] != 'Ignorada']

    df = chile_df.groupby([
        'glosa_reg_res', 'glosa_comuna_residencia', 'fecha_def'
    ])['ano_def'].count().rename('defunciones')
    df = df.reset_index()
    df.columns = ['adm1_name', 'adm3_name', 'date', 'deaths']

    df['adm3_name'] = df['adm3_name'].replace({
        'Coihaique': 'Coyhaique',
        'Ránquil': 'Ranquil',
        'Los Ángeles': 'Los Angeles',
        'Aisén': 'Aysén',
        'Los Álamos': 'Los Alamos',
    })
    df = df[df['adm3_name'] != 'Antártica']

    global geo_sa_df, sa_cities

    geo_sa_df_ = geo_sa_df.loc['Chile'].reset_index(['name_2'])['name_2']
    geo_sa_df_ = geo_sa_df_.reset_index('name_1', drop=True)
    geo_sa_df_.index = geo_sa_df_.index.map(unidecode.unidecode).str.lower()

    df['adm2_name'] = geo_sa_df_[
        df['adm3_name'].str.lower().apply(unidecode.unidecode)
    ].values

    df_deaths = df[['adm1_name', 'date', 'deaths']]
    df_deaths = df_deaths.groupby(['adm1_name', 'date']).sum()
    df_deaths = df_deaths.sort_index()
    df_deaths = storage_format(
        df_deaths,
        iso_code='CL',
        frequency='daily',
        country_name='Chile'
    )
    df_deaths = df_deaths[DF_ADM1_COLS]

    df_cities = df.drop('adm3_name', axis=1)
    df_cities = df_cities.groupby(['adm1_name', 'adm2_name', 'date']).sum()

    cities = sa_cities[sa_cities['name_0'] == 'Chile']['name_2']
    cities = pd.concat([cities, cities.apply(unidecode.unidecode)]).drop_duplicates()

    df_cities = df_cities.reindex(cities.unique(), level='adm2_name').dropna()

    df_cities = df_cities.sort_index()
    df_cities = storage_format(
        df_cities,
        iso_code='CL',
        frequency='daily',
        country_name='Chile'
    )
    df_cities = df_cities[DF_ADM2_COLS]

    return {
        'south.america.subnational.mortality': df_deaths,
        'south.america.cities.mortality': df_cities
    }


BR_BASE_URL = 'https://transparencia.registrocivil.org.br/especial-covid'
BR_STATES_URL = 'https://raw.githubusercontent.com/datasets-br/state-codes/master/data/br-state-codes.csv'
BR_STATES_FETCH_URL = 'https://transparencia.registrocivil.org.br/api/covid-cardiaco'

BR_STATES = 'AC AL AM AP BA CE DF ES GO MA MG MS MT PA PB PE PI PR RJ RN RO RR RS SC SE SP TO'.split(' ')
BR_STATES_PARAMS = {
    'start_date': '2020-03-16',
    'end_date': pd.to_datetime('now').strftime('%Y-%m-%d'),
    'state': 'all',
    'city_id': 'all',
    'chart': 'chartCardiac4',
    'places[]': [
        'HOSPITAL', 'DOMICILIO', 'VIA_PUBLICA', 'OUTROS'
    ],
    'diffCity': False,
    'cor_pele': 'I'
}
def update_brazil():
    # TODO: cities!
    state_codes = pd.read_csv(BR_STATES_URL)
    state_codes = state_codes.set_index('subdivision')

    session = requests.session()

    session_headers = perkins.DEFAULT_HEADERS.copy()
    session.get(
        BR_BASE_URL,
        headers=session_headers,
        timeout=30,
        verify=False,
    )

    if 'XSRF-TOKEN' in session.cookies:
        session_headers['XSRF-TOKEN'] = session.cookies['XSRF-TOKEN']

    df = None
    for state in BR_STATES:
        states_params = BR_STATES_PARAMS.copy()
        states_params['state'] = state

        req = session.get(
            BR_STATES_FETCH_URL,
            params=states_params,
            headers=session_headers,
            timeout=90,
            verify=False,
        )
        reqj = req.json()

        if 'chart' not in reqj:
            continue

        data = pd.DataFrame.from_dict(reqj['chart'])
        data = data.applymap(
            lambda _: _[0]['total'] if type(_) == list else _
        ).T
        data.index = pd.to_datetime(data.index)

        data = data.sum(axis=1)
        data = data.reset_index()
        data.columns = ['date', 'deaths']
        data['adm1_name'] = state

        df = pd.concat([df, data])
        time.sleep(1.)

    df['adm1_name'] = df['adm1_name'].map(
        state_codes['name'].to_dict()
    )

    df = df.set_index(['adm1_name', 'date'])
    df = df.sort_index()

    df_deaths = storage_format(
        df,
        iso_code='BR',
        frequency='daily',
        country_name='Brazil'
    )
    df_deaths = df_deaths[DF_ADM1_COLS]

    return {
        'south.america.subnational.mortality': df_deaths,
    }


ECU_PROVINCIAS_MAP = {
    'Santo Domingo de los Tsachilas': 'Santo Domingo de los Tsachilas',
    'Sto Dgo Tsachil': 'Santo Domingo de los Tsachilas',
    'Sto Domingo Tsachilas': 'Santo Domingo de los Tsachilas'
}
ECU_CANTONES_MAP = {
    'Alfredo Baquerizo Moreno (jujan)': 'Alfredo Baquerizo Moreno',
    'Baños de Agua Santa': 'Baños',
    'El Empalme': 'Empalme',
    'Francisco de Orellana': 'Orellana',
    'General Villamil (playas)': 'Playas',
    'Rio Verde': 'Rioverde',
    'Yaguachi': 'San Jacinto de Yaguachi'
}
ECUADOR_URL = 'https://www.registrocivil.gob.ec/registro-civil-del-ecuador-cifras-de-defunciones/'
def update_ecuador():
    # proxy = perkins.requests.setup_proxy(ECUADOR_URL, country='Ecuador')
    proxy = None

    cdata = perkins.requests.do_request(
        ECUADOR_URL,
        verify=False,
        timeout=60,
        headers=perkins.DEFAULT_HEADERS,
        proxies=proxy,
    )
    cdata = BeautifulSoup(cdata.text, 'html.parser')

    cdata_btns = cdata.find_all('tr')
    download_url = next(
        _ for _ in cdata_btns if 'defunciones generales' in _.text.lower()
    ).findChild('a').attrs['href']

    cdata = perkins.requests.do_request(
        download_url,
        verify=False,
        timeout=60,
        headers=perkins.DEFAULT_HEADERS,
        proxies=proxy,
    )

    dept_engine = 'xlrd'
    if (
        download_url.endswith('xlsx') or
        (
            'Content-Location' in cdata.headers and
            cdata.headers['Content-Location'].endswith('xlsx')
        )
    ):
        dept_engine = 'openpyxl'
    elif download_url.endswith('xlsb'):
        dept_engine = 'pyxlsb'

    df = pd.read_excel(cdata.content, engine=dept_engine, header=None)
    df = df[~df.isna().all(axis=1)]

    try:
        df_columns = [_.encode('cp1252').decode('utf-8') for _ in df.iloc[0]]
    except:
        df_columns = df.iloc[0]

    df_columns = [unidecode.unidecode(_) for _ in df_columns]
    df.columns = [_.lower().replace(' ', '_') for _ in df_columns]
    df = df.iloc[1:]

    df = df[df['zona'] != 'ND']
    df = df.drop(['zona', 'mes', 'dia'], axis=1)
    df.iloc[:, :3] = df.iloc[:, :3].applymap(do_title)

    df['provincia'] = df['provincia'].replace(ECU_PROVINCIAS_MAP)
    df['canton'] = df['canton'].replace(ECU_CANTONES_MAP)

    if (df['fecha_defuncion'].dtype == object):
        if (
            (df['fecha_defuncion'].str.contains('-').sum() / df.shape[0] > .9) or
            (df['fecha_defuncion'].str.contains('/').sum() / df.shape[0] > .9)
        ):
            df['fecha_defuncion'] = pd.to_datetime(df['fecha_defuncion'])
        else:
            fd_t = df['fecha_defuncion'].apply(type)
            if (fd_t == int).sum() / len(fd_t) > .9:
                df = df[fd_t == int]

            df['fecha_defuncion'] = df['fecha_defuncion'].astype(np.int64)

    if df['fecha_defuncion'].dtype == np.int64:
        df_td = df['fecha_defuncion'].apply(
            lambda _: pd.Timedelta(days=_)
        )
        df['fecha_defuncion'] = pd.to_datetime('1899/12/30') + df_td

    df = df[df['fecha_defuncion'] >= '2025-01-01']
    df = df.groupby([
        'provincia', 'canton', 'fecha_defuncion'
    ])['parroquia'].count()

    df = df.reset_index()
    df.columns = ['adm1_name', 'adm2_name', 'date', 'deaths']

    df_deaths = df.groupby(['adm1_name', 'date']).sum()
    df_deaths = df_deaths.sort_index()
    df_deaths = storage_format(
        df_deaths,
        iso_code='EC',
        frequency='daily',
        country_name='Ecuador'
    )
    df_deaths = df_deaths[DF_ADM1_COLS]

    global sa_cities

    cities = sa_cities[sa_cities['name_0'] == 'Ecuador']['name_2']
    cities = pd.concat([cities, cities.apply(unidecode.unidecode)]).drop_duplicates()

    df_cities = df.set_index(['adm1_name', 'adm2_name', 'date'])
    df_cities = df_cities.reindex(cities.unique(), level='adm2_name').dropna()

    df_cities = df_cities.sort_index()
    df_cities = storage_format(
        df_cities,
        iso_code='EC',
        frequency='daily',
        country_name='Ecuador'
    )
    df_cities = df_cities[DF_ADM2_COLS]

    return {
        'south.america.subnational.mortality': df_deaths,
        'south.america.cities.mortality': df_cities
    }


COLOMBIA_TOKEN = 'eyJrIjoiNzU4ZjUwNGEtNjlhNy00NmU4LWJmYTktYTY1YTZiMGFkNjIyIiwidCI6ImJmYjdlMTNhLTdmYjctNDAxNi04MzBjLWQzNzE2ZThkZDhiOCJ9'
COLOMBIA_API_URL = 'https://wabi-paas-1-scus-api.analysis.windows.net'
COLOMBIA_API_URL = COLOMBIA_API_URL + '/public/reports/querydata?synchronous=true'
def update_colombia():
    # This may fail in the future due to a change in the dashboards' uri
    # TODO: fetch directly from: https://experience.arcgis.com/experience/d9bfa6a650a249099b5f290a6c454804/?draft=true
    resource_key = json.loads(base64.b64decode(COLOMBIA_TOKEN))['k']

    headers = perkins.DEFAULT_HEADERS.copy()
    headers['X-PowerBI-ResourceKey'] = resource_key
    headers['RequestId'] = str(uuid.uuid4())

    CONNECTION = {
        'application_context': {
            'DatasetId': '1c8b60ae-edc0-47fb-94e9-28cf505f2e36',
            'Sources': [{
                'ReportId': '7e45edd0-e762-4036-a8c9-5505a82ae12a',
                'VisualId': 'f868698455f8dcb10e52'
            }]
        },
        'model_id': 1699279
    }

    TABLES = {
        'calendario': {'Name': 'c', 'Entity': 'calendario', 'Type': 0},
        'Divipola': {'Name': 'd', 'Entity': 'Divipola', 'Type': 0},
        'Medidas': {'Name': 'm', 'Entity': 'Medidas', 'Type': 0},
        'Lugar': {'Name': 't', 'Entity': 'Tbl_Ocurrencia_defuncion', 'Type': 0},
    }
    FROM_TABLES = list(TABLES.values())

    do_build_fields = lambda table, fields: (
        [perkins.input.powerbi.build_fields(TABLES[table], field) for field in fields]
    )

    SELECT_COLUMNS = [
        do_build_fields('calendario', ['Date']),
        do_build_fields('Divipola', ['Departamento']),
        [
            perkins.input.powerbi.build_fields(
                TABLES['Medidas'], _, type='Measure'
            ) for _ in ['Conteo_def_Año_Actual']
        ],
    ]
    SELECT_COLUMNS = itertools.chain(*SELECT_COLUMNS)
    SELECT_COLUMNS = list(SELECT_COLUMNS)

    WHERE = [
        perkins.input.powerbi.build_where( # año >= 2021
            TABLES['calendario'],
            column='año',
            value='2022L'
        ),
        perkins.input.powerbi.build_where( # fallecidos > 0
            TABLES['Medidas'],
            column='Conteo_def_Año_Actual',
            value='0L',
            kind=1,
            type='Measure'
        ),
        perkins.input.powerbi.build_where( # tomar datos por lugar de ocurrencia
            TABLES['Lugar'],
            column='lugar_defuncion',
            value="'Cod_mun_Ocurrencia'",
            condition='In'
        ),
    ]

    QUERY = perkins.input.powerbi.build_query(
        CONNECTION, FROM_TABLES, SELECT_COLUMNS, WHERE, []
    )

    data = requests.post(
        COLOMBIA_API_URL, json=QUERY, headers=headers, timeout=90
    )
    data = data.json()

    df = perkins.input.powerbi.inflate_data(
        data, ['date', 'adm1_name', 'deaths']
    )
    df = df.replace('', np.nan).fillna(method='ffill')
    df['date'] = pd.to_datetime(df['date'], unit='ms')

    df['adm1_name'] = df['adm1_name'].replace({
        'BOGOTÁ, D. C.': 'BOGOTA',
        'ARCHIPIÉLAGO DE SAN ANDRÉS, PROVIDENCIA Y SANTA CATALINA': 'SAN ANDRES Y PROVIDENCIA'
    })

    df = df.groupby(['adm1_name', pd.Grouper(key='date', freq='W')]).sum()
    df = df.unstack(level=0).iloc[1:].T.stack().rename('deaths').to_frame()
    df = storage_format(
        df,
        iso_code='CO',
        frequency='weekly',
        country_name='Colombia'
    )
    df['date'] = df['date'] - pd.Timedelta(days=6)

    return {
        'south.america.subnational.mortality': df,
    }


PERU_URL = 'https://files.minsa.gob.pe/s/Ae52gBAMf9aKEzK/download/SINADEF_DATOS_ABIERTOS.csv'
def update_peru():
    cdata = requests.get(PERU_URL, headers=perkins.DEFAULT_HEADERS)
    df = pd.read_csv(io.BytesIO(cdata.content), encoding='utf-8', on_bad_lines='skip')

    df['FECHA'] = pd.to_datetime(df['FECHA'], dayfirst=True)
    df = df.sort_values('FECHA')

    df = df[df['PAIS_DOMICILIO'] == 'PERU']

    df['DEPARTAMENTO_DOMICILIO'] = df['DEPARTAMENTO_DOMICILIO'].str.strip()
    df = df[df['DEPARTAMENTO_DOMICILIO'].astype(bool)]
    df['PROVINCIA_DOMICILIO'] = df['PROVINCIA_DOMICILIO'].str.strip()
    df = df[df['PROVINCIA_DOMICILIO'].astype(bool)]

    df = df.groupby([
        'DEPARTAMENTO_DOMICILIO', 'PROVINCIA_DOMICILIO', 'FECHA'
    ])[df.columns[0]].count().reset_index()
    df.columns = ['adm1_name', 'adm2_name', 'date', 'deaths']

    df_deaths = df.groupby(['adm1_name', 'date'])['deaths'].sum()
    df_deaths = df_deaths.sort_index()

    # Patch Drop Locations: EXTRANJERO/SIN REGISTRO
    df_deaths = df_deaths.drop('EXTRANJERO', level=0, errors='ignore')
    df_deaths = df_deaths.drop('SIN REGISTRO', level=0, errors='ignore')

    df_deaths = storage_format(
        df_deaths,
        iso_code='PE',
        frequency='daily',
        country_name='Peru'
    )
    df_deaths = df_deaths[DF_ADM1_COLS]

    global sa_cities
    cities = sa_cities[sa_cities['name_0'] == 'Peru']['name_2']
    cities = pd.concat([cities, cities.apply(unidecode.unidecode)]).drop_duplicates()

    df['adm2_name'] = df['adm2_name'].str.lower().str.title()

    df_cities = df.set_index(['adm1_name', 'adm2_name', 'date'])
    df_cities = df_cities.reindex(cities.unique(), level='adm2_name').dropna()

    df_cities = df_cities.sort_index()
    df_cities = storage_format(
        df_cities,
        iso_code='PE',
        frequency='daily',
        country_name='Peru'
    )
    df_cities = df_cities[DF_ADM2_COLS]

    return {
        'south.america.subnational.mortality': df_deaths,
        'south.america.cities.mortality': df_cities
    }


PARAGUAY_DEPTS = {
  '01': 'Concepción',
  '02': 'San Pedro',
  '03': 'Cordillera',
  '04': 'Guairá',
  '05': 'Caaguazú',
  '06': 'Caazapá',
  '07': 'Itapúa',
  '08': 'Misiones',
  '09': 'Paraguarí',
  '10': 'Alto Paraná',
  '11': 'Central',
  '12': 'Ñeembucú',
  '13': 'Amambay',
  '14': 'Canindeyú',
  '15': 'Presidente Hayes',
  '16': 'Boquerón',
  '17': 'Alto Paraguay',
  '18': 'Asunción'
}
PARAGUAY_URL = 'https://ssiev.mspbs.gov.py/20220618/defuncion_reportes/lista_multireporte_defuncion.php'
PARAGUAY_DATA = {
    'elegido': 2,
    'xfila': 'coddist',
    'xcolumna': 'EXTRACT(MONTH FROM  fechadef)',
    'anio1': 2021,
    'anio2': 2021,
    'coddpto': None
}
def do_download_paraguay(dept_code, year=2021):
    data = {
        **PARAGUAY_DATA,
        'anio1': year,
        'anio2': year,
        'coddpto': dept_code
    }
    cdata = requests.post(PARAGUAY_URL, data=data)

    df = pd.read_html(
        io.BytesIO(cdata.content), flavor='html5lib', encoding='utf-8'
    )[0]
    df = df.drop(0)

    # Parse HTML format

    df.columns = df.iloc[0]
    df = df.iloc[1:]

    df = df.set_index('Lugar de Defunción/Distrito')
    df = df.drop(['Total', 'EXTRANJERO'], errors='ignore')

    df = df.iloc[:, :-1]

    df = df.applymap(lambda _: int(str(_).replace('.', '')))
    df = df[df.columns[df.sum() > 0]]

    df = df.unstack().reset_index()
    df.columns = ['month', 'lugar', 'deaths']

    df['year'] = year
    df = df[['lugar', 'year', 'month', 'deaths']]

    df['month'] = df['month'].replace({
        'Enero': 1, 'Febrero': 2, 'Marzo': 3,
        'Abril': 4, 'Mayo': 5, 'Junio': 6,
        'Julio': 7, 'Agosto': 8, 'Septiembre': 9, 'Setiembre': 9,
        'Octubre': 10, 'Noviembre': 11, 'Diciembre': 12
    })

    # format

    df['date'] = df[['year', 'month']].apply(
       lambda _: '{}-{}-1'.format(_['year'], _['month']), axis=1
    )
    df['date'] = pd.to_datetime(df['date'])

    df = df.groupby(['lugar', 'date'])['deaths'].sum()
    df = df.reset_index()

    df.columns = ['adm2_name', 'date', 'deaths']
    df['adm2_name'] = df['adm2_name'].str.lower().str.title()
    df['adm2_name'] = df['adm2_name'].str.replace(
        ' De ', ' de '
    ).str.replace(
        ' Del ', ' del '
    ).str.replace(
        ' El ', ' el ',
    ).str.replace(
        ' La ', ' la ',
    )

    return df


def update_paraguay():
    df = pd.DataFrame([])
    current_year = pd.to_datetime('now').year

    for year in np.arange(current_year - 1, current_year + 1):
        for dept_code, adm1_name in PARAGUAY_DEPTS.items():
            try:
                dept_df = do_download_paraguay(dept_code, year=year)
                dept_df['adm1_name'] = adm1_name

            except Exception as e:
                dept_df = None

            df = pd.concat([df, dept_df])

    df['adm2_name'] = df['adm2_name'].replace({
        'Mariscal Estigarribia': 'Mariscal Jose Felix Estigarribia'
    })
    df = df[np.roll(df.columns, 1)]

    df_deaths = df.groupby(['adm1_name', 'date']).sum()
    df_deaths = df_deaths.sort_index()
    df_deaths = storage_format(
        df_deaths,
        iso_code='PY',
        frequency='monthly',
        country_name='Paraguay'
    )
    df_deaths = df_deaths[DF_ADM1_COLS]

    global sa_cities
    cities = sa_cities[sa_cities['name_0'] == 'Paraguay']['name_2']
    cities = pd.concat([cities, cities.apply(unidecode.unidecode)]).drop_duplicates()

    df_cities = df.set_index(['adm1_name', 'adm2_name', 'date'])
    df_cities = df_cities.reindex(cities.unique(), level='adm2_name').dropna()

    df_cities = df_cities.sort_index()
    df_cities = storage_format(
        df_cities,
        iso_code='PY',
        frequency='monthly',
        country_name='Paraguay'
    )
    df_cities = df_cities[DF_ADM2_COLS]

    return {
        'south.america.subnational.mortality': df_deaths,
        'south.america.cities.mortality': df_cities
    }


BOLIVIA_URL = 'https://raw.githubusercontent.com/sociedatos/bo-mortalidad/main/registro.civil.csv'
BOLIVIA_LOC_MAP = {
    '1': 'Chuquisaca',
    '2': 'La Paz',
    '3': 'Cochabamba',
    '4': 'Oruro',
    '5': 'Potosi',
    '6': 'Tarija',
    '7': 'Santa Cruz',
    '8': 'El Beni',
    '9': 'Pando'
}
def update_bolivia():
    df = pd.read_csv(BOLIVIA_URL)

    df['fecha'] = pd.to_datetime(df['fecha'])
    df = df.groupby([
        df['cod_ine'].astype(str).str[0], 'fecha'
    ])['decesos'].sum().reset_index()
    df.columns = ['adm1_name', 'date', 'deaths']

    df['adm1_name'] = df['adm1_name'].replace(BOLIVIA_LOC_MAP)

    df = df.set_index(['adm1_name', 'date'])
    df = df.sort_index()
    df = storage_format(
        df,
        iso_code='BO',
        frequency='monthly',
        country_name='Bolivia'
    )

    df['adm1_name'] = df['adm1_name'].str.replace(
        'El Beni', 'Beni'
    )

    return {
        'south.america.subnational.mortality': df,
    }


def do_update(fn):
    print(fn.__name__)

    try:
        df_objs = fn()
    except Exception as e:
        traceback.print_exc()
        df_objs = {}

    # >= 2021-07-31
    for key, df in df_objs.items():
        # df = df[df['date'] > '2021-07-31'].copy()

        df['deaths'] = df['deaths'].astype(int)
        df['date'] = pd.to_datetime(df['date'])

        df_objs[key] = df

    return df_objs


STORAGE_FILE = './{}.csv'
DF_NON_INDEX_COLS = ['country_name', 'adm1_isocode', 'frequency', 'deaths']
def do_merge(df, path):
    file_name = STORAGE_FILE.format(path)
    base_df = pd.read_csv(file_name)

    order_cols = base_df.columns
    index_cols = [_ for _ in order_cols if _ not in DF_NON_INDEX_COLS]

    base_df['date'] = pd.to_datetime(base_df['date'])
    base_df = base_df.set_index(index_cols)

    df = df.set_index(index_cols)
    df = pd.concat([base_df, df])

    df = df[~df.index.duplicated(keep='last')]
    df = df.sort_index()

    df = df.reset_index()
    df = df[order_cols]
    df['date'] = pd.to_datetime(df['date']).dt.date

    df.to_csv(file_name, index=False)


UPDATE_FNS = [
    update_chile,
    update_brazil,
    update_ecuador,
    update_colombia,
    update_peru,
    update_paraguay,
    update_bolivia
]
if __name__ == '__main__':
    iso_level_0, iso_geo_names, geo_names = perkins.fetch_geocodes()
    geo_sa_df, sa_cities = get_population()
    final_df = {}

    for update_fn in UPDATE_FNS:
        df_objs = do_update(update_fn)

        for key, df in df_objs.items():
            fdf = final_df.get(key, None)
            final_df[key] = pd.concat([fdf, df])

    for key, df in final_df.items():
        do_merge(df, key)
