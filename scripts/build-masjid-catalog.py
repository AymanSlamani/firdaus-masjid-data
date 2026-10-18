#!/usr/bin/env python3
import argparse
import json
from pathlib import Path
from tzfpy import get_tz
from masjid_data.merge import build_catalog
from masjid_data.geography import CensusStates


def atomic_json(path, value):
    path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')
    temporary.replace(path)


def main():
    p=argparse.ArgumentParser(description='Merge independently imported, attributed public source snapshots.')
    p.add_argument('--work-dir',type=Path,default=Path('.tools/masjid-data'))
    p.add_argument('--osm',type=Path,default=Path('assets/data/masjids/osm-us.json'))
    p.add_argument('--output',type=Path,default=Path('assets/data/masjids/us.json'))
    p.add_argument('--audit',type=Path,default=Path('docs/masjid-coverage.json'))
    a=p.parse_args()
    schedules_file=a.work_dir/'schedules.json'
    schedules=json.loads(schedules_file.read_text()).get('schedules',{}) if schedules_file.exists() else {}
    extra=[]
    esalah=a.work_dir/'esalah.json'
    if esalah.exists():
        data=json.loads(esalah.read_text());data['sourceName']='esalah';extra.append(data)
    discovery_file=a.work_dir/'discovery.json'
    discovery=json.loads(discovery_file.read_text()) if discovery_file.exists() else {}
    states=CensusStates(a.work_dir/'us-states.zip')
    catalog,audit=build_catalog(json.loads(a.osm.read_text()),json.loads((a.work_dir/'usa.json').read_text()),json.loads((a.work_dir/'masjidal.json').read_text()),timezone_for=get_tz,schedules=schedules,state_for=states.state,extra_sources=extra,discovery=discovery)
    if len(catalog['masjids'])<3000: raise ValueError('Catalog unexpectedly shrank below 3,000 records; refusing to replace it')
    atomic_json(a.output,catalog);atomic_json(a.audit,audit)
    atomic_json(a.work_dir/'catalog.json',catalog)
    print(json.dumps({k:v for k,v in audit.items() if k not in {'merges','rejectedOrReview','limits'}},indent=2))


if __name__=='__main__':main()
