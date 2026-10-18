"""Publication checks for public catalog shape, provenance, and privacy."""
import datetime as dt
import json
from pathlib import Path
from zoneinfo import ZoneInfo


def validate_catalog(path):
    data=json.loads(Path(path).read_text())
    assert data['schemaVersion']==2 and data['country']=='US'
    rows=data['masjids'];assert 3000<=len(rows)<=20000
    seen=set();forbidden={'email','contributor_url','contributorUrl','rating','reviews','photos','raw_html','rawHtml','cookies','token','apikey'}
    def inspect(value):
        if isinstance(value,dict):
            assert not forbidden.intersection(value), 'Non-public or unnecessary field in publication'
            for child in value.values():inspect(child)
        elif isinstance(value,list):
            for child in value:inspect(child)
        elif isinstance(value,str):
            assert '/Users/' not in value and 'ghp_' not in value and 'github_pat_' not in value
    inspect(data)
    for row in rows:
        assert row['id'] not in seen;seen.add(row['id'])
        assert row['country']=='US' and row['name'].strip() and row['sources']
        assert -90<=row['lat']<=90 and -180<=row['lng']<=180
        ZoneInfo(row['zone'])
        for source in row['sources']:assert source['url'].startswith(('https://','http://'))
        schedule=row.get('publishedSchedule')
        if schedule:
            dt.date.fromisoformat(schedule['date']);dt.datetime.fromisoformat(schedule['retrievedAt'])
            assert schedule['provider'] in row.get('providers',{})
            assert schedule['providerId']==row['providers'][schedule['provider']]
            assert all(isinstance(v,int) and 0<=v<1440 for v in schedule['iqamah'].values())
            assert all(isinstance(v,int) and 0<=v<1440 for v in schedule.get('jumuah',[]))
    regions={r['region'] for r in rows}
    assert {'AK','HI','NY','CA','TX','DC'}.issubset(regions)
    return data
