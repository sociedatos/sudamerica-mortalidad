#!/usr/bin/env python3
# coding: utf-8

import io
import json
import uuid
import base64
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


CHILE_URL = 'https://github.com/MinCiencia/Datos-COVID19/blob/master/output/producto32/Defunciones.csv?raw=true'
def update_chile():
    df = pd.read_csv(CHILE_URL)
    df = df.set_index(['Region', 'Codigo region', 'Comuna', 'Codigo comuna'])
    df.columns = pd.to_datetime(df.columns)

    df = df.stack()
    df = df.reset_index()

    df = df[['Region', 'Comuna', 'level_4', 0]]
    df.columns = ['adm1_name', 'adm3_name', 'date', 'deaths']

    df['adm3_name'] = df['adm3_name'].replace({'Coihaique': 'Coyhaique'})
    df = df.sort_index().groupby(['adm1_name', 'adm3_name', 'date']).sum()
    df = df.reset_index()

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
    df_cities = df_cities.loc[pd.IndexSlice[:, cities], :]

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


def do_download_brazil(URL, fields):
    df = pd.read_csv(URL)
    df['date'] = pd.to_datetime(df['date'])

    drop_columns = [_ + '_ibge_code' for _ in fields]
    df = df.drop(drop_columns + ['place'], axis=1)

    df = df.groupby(fields + ['date']).sum().sum(axis=1)
    df = df.reset_index()

    return df


BRAZIL_STATES_URL = 'https://raw.githubusercontent.com/datasets-br/state-codes/master/data/br-state-codes.csv'
BRAZIL_URL = 'https://github.com/capyvara/brazil-civil-registry-data/blob/master/civil_registry_covid_states.csv?raw=true'
BRAZIL_CITIES_URL = 'https://github.com/capyvara/brazil-civil-registry-data/blob/master/civil_registry_covid_cities.csv?raw=true'
def update_brazil():
    state_codes = pd.read_csv(BRAZIL_STATES_URL)
    state_codes = state_codes.set_index('subdivision')

    df = do_download_brazil(BRAZIL_URL, ['state'])
    df.columns = ['adm1_name', 'date', 'deaths']

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

    df = do_download_brazil(BRAZIL_CITIES_URL, ['state', 'city'])
    df.columns = ['adm1_name', 'adm2_name', 'date', 'deaths']

    df['adm1_name'] = df['adm1_name'].map(
        state_codes['name'].to_dict()
    )

    df = df.set_index(['adm1_name', 'adm2_name', 'date'])
    df = df.sort_index()

    df_cities = storage_format(
        df,
        iso_code='BR',
        frequency='daily',
        country_name='Brazil'
    )
    df_cities = df_cities[DF_ADM2_COLS]

    return {
        'south.america.subnational.mortality': df_deaths,
        'south.america.cities.mortality': df_cities
    }


ECU_PROVINCIAS_MAP = {
    'Santo Domingo de los Tsachilas': 'Santo Domingo de los Tsachilas',
    'Sto Dgo Tsachil': 'Santo Domingo de los Tsachilas',
    'Sto Domingo Tsachilas': 'Santo Domingo de los Tsachilas'
}
ECU_CANTONES_MAP = {
    'Alfredo Baquerizo Moreno (jujan)': 'Alfredo Baquerizo Moreno',
    'Ba??os de Agua Santa': 'Ba??os',
    'El Empalme': 'Empalme',
    'Francisco de Orellana': 'Orellana',
    'General Villamil (playas)': 'Playas',
    'Rio Verde': 'Rioverde',
    'Yaguachi': 'San Jacinto de Yaguachi'
}
ECUADOR_URL = 'https://www.registrocivil.gob.ec/cifras_defunciones_2022/'
def update_ecuador():
    cdata = requests.get(
        ECUADOR_URL,
        verify=False,
        headers=perkins.DEFAULT_HEADERS,
        timeout=120
    )
    cdata = BeautifulSoup(cdata.text, 'html.parser')

    cdata_btns = cdata.find_all('tr')
    download_url = next(
        _ for _ in cdata_btns if 'defunciones generales' in _.text.lower()
    ).findChild('a').attrs['href']

    cdata = perkins.requests.do_request(
        download_url,
        verify=False,
        headers=perkins.DEFAULT_HEADERS,
        timeout=30
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

    df = pd.read_excel(cdata.content, engine=dept_engine, header=None)
    df = df[~df.isna().all(axis=1)]

    try:
        df_columns = [_.encode('cp1252').decode('utf-8') for _ in df.iloc[0]]
    except:
        df_columns = df.iloc[0]

    df_columns = [unidecode.unidecode(_) for _ in df_columns]
    df.columns = [_.lower().replace(' ', '_') for _ in df_columns]
    df = df.iloc[1:]

    df = df.drop(['zona', 'mes_def', 'dia_def'], axis=1)
    df.iloc[:, :3] = df.iloc[:, :3].applymap(do_title)

    df['provincia_defuncion'] = df['provincia_defuncion'].replace(ECU_PROVINCIAS_MAP)
    df['canton_defuncion'] = df['canton_defuncion'].replace(ECU_CANTONES_MAP)

    if df['fecha_defuncion'].dtype == np.int64:
        df_td = df['fecha_defuncion'].apply(
            lambda _: pd.Timedelta(days=_)
        )
        df['fecha_defuncion'] = pd.to_datetime('1899/12/30') + df_td

    df = df.groupby([
        'provincia_defuncion', 'canton_defuncion', 'fecha_defuncion'
    ])['parroquia_defuncion'].count()

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

    df_cities = df.set_index(['adm1_name', 'adm2_name'])
    df_cities = df_cities.loc[pd.IndexSlice[:, cities], :]

    df_cities = df_cities.set_index('date', append=True)
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
            ) for _ in ['Conteo_def_A??o_Actual']
        ],
    ]
    SELECT_COLUMNS = itertools.chain(*SELECT_COLUMNS)
    SELECT_COLUMNS = list(SELECT_COLUMNS)

    WHERE = [
        perkins.input.powerbi.build_where( # a??o >= 2021
            TABLES['calendario'],
            column='a??o',
            value='2021L'
        ),
        perkins.input.powerbi.build_where( # fallecidos > 0
            TABLES['Medidas'],
            column='Conteo_def_A??o_Actual',
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
        'BOGOT??, D. C.': 'BOGOTA',
        'ARCHIPI??LAGO DE SAN ANDR??S, PROVIDENCIA Y SANTA CATALINA': 'SAN ANDRES Y PROVIDENCIA'
    })

    df = df.groupby(['adm1_name', pd.Grouper(key='date', freq='W')]).sum()
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


PERU_URL = 'https://cloud.minsa.gob.pe/s/nqF2irNbFomCLaa/download'
def update_peru():
    cdata = requests.get(PERU_URL, headers=perkins.DEFAULT_HEADERS)
    df = pd.read_csv(
        io.BytesIO(cdata.content),
        delimiter='|',
        encoding='utf-8'
    )

    df['FECHA'] = pd.to_datetime(df['FECHA'])
    df = df.sort_values('FECHA')

    df = df[df['PAIS DOMICILIO'] == 'PERU']

    df['DEPARTAMENTO DOMICILIO'] = df['DEPARTAMENTO DOMICILIO'].str.strip()
    df = df[df['DEPARTAMENTO DOMICILIO'].astype(bool)]
    df['PROVINCIA DOMICILIO'] = df['PROVINCIA DOMICILIO'].str.strip()
    df = df[df['PROVINCIA DOMICILIO'].astype(bool)]

    df = df.groupby([
        'DEPARTAMENTO DOMICILIO', 'PROVINCIA DOMICILIO', 'FECHA'
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

    df_cities = df.set_index(['adm1_name', 'adm2_name'])
    df_cities = df_cities.loc[pd.IndexSlice[:, cities], :]

    df_cities = df_cities.set_index('date', append=True)
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
  '01': 'Concepci??n',
  '02': 'San Pedro',
  '03': 'Cordillera',
  '04': 'Guair??',
  '05': 'Caaguaz??',
  '06': 'Caazap??',
  '07': 'Itap??a',
  '08': 'Misiones',
  '09': 'Paraguar??',
  '10': 'Alto Paran??',
  '11': 'Central',
  '12': '??eembuc??',
  '13': 'Amambay',
  '14': 'Canindey??',
  '15': 'Presidente Hayes',
  '16': 'Boquer??n',
  '17': 'Alto Paraguay',
  '18': 'Asunci??n'
}
PARAGUAY_URL = 'http://ssiev.mspbs.gov.py/20220618/defuncion_reportes/lista_multireporte_defuncion.php'
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

    df = df.set_index('Lugar de Defunci??n/Distrito')
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

    for year in [2022]:
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

    df_cities = df.set_index(['adm1_name', 'adm2_name'])
    df_cities = df_cities.loc[pd.IndexSlice[:, cities], :]

    df_cities = df_cities.set_index('date', append=True)
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
def update_bolivia():
    df = pd.read_csv(BOLIVIA_URL, index_col=0)

    df.index = pd.to_datetime(df.index)
    df = df.unstack().reset_index()
    df.columns = ['adm1_name', 'date', 'deaths']

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
        df = df[df['date'] > '2021-07-31'].copy()

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
