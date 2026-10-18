#!/usr/bin/env python3
"""Run the hosted public-data repository's daily or full refresh transaction."""
import argparse
import datetime as dt
import json
from pathlib import Path
import shutil
import subprocess
import sys
from masjid_data.validate import validate_catalog


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--full',action='store_true');a=p.parse_args()
    root=Path.cwd();work=root/'.tools/masjid-data';inputs=root/'inputs';catalog=root/'catalog'
    work.mkdir(parents=True,exist_ok=True)
    for path in inputs.glob('*.json'):shutil.copy2(path,work/path.name)
    shutil.copy2(inputs/'us-states.zip',work/'us-states.zip')
    def run(script,*args):subprocess.run([sys.executable,str(root/'scripts'/script),*map(str,args)],check=True,cwd=root)
    run('refresh-masjids.py','--work-dir',work)
    # Update the country boundary import weekly; previous valid OSM data remains
    # an input until a complete replacement succeeds.
    if a.full:run('import-masjids.py','--output',inputs/'osm-us.json')
    run('fetch-masjid-schedules.py','--work-dir',work)
    def build():run('build-masjid-catalog.py','--work-dir',work,'--osm',inputs/'osm-us.json','--output',catalog/'us.json','--audit',catalog/'coverage.json')
    build()
    if a.full:
        run('import-esalah-masjids.py','--work-dir',work,'--catalog',catalog/'us.json')
        build()
        run('discover-masjid-providers.py','--work-dir',work,'--catalog',catalog/'us.json')
    if (work/'discovery.json').exists():run('fetch-discovered-schedules.py','--work-dir',work,'--catalog',catalog/'us.json')
    build();data=validate_catalog(catalog/'us.json')
    for name in ['usa.json','masjidal.json','esalah.json','discovery.json','schedules.json']:
        path=work/name
        if path.exists():shutil.copy2(path,inputs/name)
    coverage=data['coverage'];rows=data['masjids']
    summary={'generatedAt':data['importedAt'],'locations':len(rows),'providerLinked':coverage['withLiveProvider'],'publishedSchedules':coverage['withPublishedSnapshot'],'states':len({r['region'] for r in rows}),'completeCensus':False}
    (catalog/'status.json').write_text(json.dumps(summary,indent=2)+'\n')
    gaps=[{'id':r['id'],'name':r['name'],'city':r['city'],'state':r['region'],'website':r.get('website'),'sources':r['sources']} for r in rows if not r.get('providers')]
    (catalog/'schedule-gaps.json').write_text(json.dumps({'generatedAt':data['importedAt'],'count':len(gaps),'locations':gaps},ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(summary,indent=2))


if __name__=='__main__':main()
