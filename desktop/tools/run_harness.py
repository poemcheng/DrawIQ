"""One-command, isolated source/contract/regression verification; no live DB access."""
import argparse
import hashlib
import json
import os
import platform
import re
import subprocess
import sys
from datetime import datetime,timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from workbench import __version__
from workbench.harness import policy
from workbench.domain import digest
from workbench.harness_validation import check_repository,evaluate_cases,source_fingerprint


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--suite',choices=['quick','full'],default='quick')
    parser.add_argument('--cases',type=Path,default=ROOT/'evaluation'/'harness_cases.json')
    parser.add_argument('--output',type=Path,default=ROOT/'qa'/f'harness_{__version__}')
    args=parser.parse_args(); stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    run=args.output/'runs'/stamp; run.mkdir(parents=True,exist_ok=False)
    before=source_fingerprint(ROOT); repository=check_repository(ROOT)
    case_hash=None
    try:
        case_bytes=args.cases.read_bytes(); case_hash=hashlib.sha256(case_bytes).hexdigest()
        payload=json.loads(case_bytes.decode('utf-8')); cases=evaluate_cases(payload)
    except (OSError,ValueError,TypeError,KeyError) as exc:
        cases={'passed':False,'case_count':0,'kind':'invalid_fixture','error':str(exc),
               'ocr_or_vlm_executed':False,'accuracy_benchmark':False}
    suite=['discover','-s','tests','-v'] if args.suite=='full' else [
        'tests.test_harness','tests.test_engineering_candidates','tests.test_quotation_semantics',
        'tests.test_workbench.ExtractionTests','-v']
    env={**os.environ,'PYTHONIOENCODING':'utf-8','QT_QPA_PLATFORM':'offscreen'}
    log=run/'tests.log'
    with log.open('w',encoding='utf-8') as stream:
        try:
            test=subprocess.run([sys.executable,'-m','unittest',*suite],cwd=ROOT,env=env,
                                stdout=stream,stderr=subprocess.STDOUT,timeout=600)
            exit_code=test.returncode
        except subprocess.TimeoutExpired:
            stream.write('\nHarness: tests exceeded 600 seconds.\n'); exit_code=124
    after=source_fingerprint(ROOT); unchanged=before==after
    count=re.search(r'Ran (\d+) tests? in ([0-9.]+)s',log.read_text(encoding='utf-8'))
    tests_passed=exit_code==0 and count is not None and int(count[1])>0
    cases_path=args.cases.resolve()
    data={'schema':'drawiq-harness-validation/1','application_version':__version__,
          'executed_at':stamp,'suite':args.suite,'python':sys.version,'platform':platform.platform(),
          'policy_sha256':digest(policy()),'source_fingerprint':before,'sources_unchanged_during_run':unchanged,
          'repository':repository,'contracts':cases,'cases_path':str(cases_path),'cases_file_sha256':case_hash,
          'tests':{'passed':tests_passed,'exit_code':exit_code,'log':'tests.log',
                   'count':int(count[1]) if count else None,'seconds':float(count[2]) if count else None,
                   'log_sha256':hashlib.sha256(log.read_bytes()).hexdigest()},
          'live_database_accessed':False,'contract_cases_execute_ocr_or_vlm':False,
          'unit_test_inputs':'synthetic/isolated fixtures; OCR execution is not counted by this runner',
          'accuracy_benchmark':False,
          'passed':repository['passed'] and cases['passed'] and tests_passed and unchanged}
    if cases_path.is_relative_to(ROOT):
        data['cases_relative_path']=cases_path.relative_to(ROOT).as_posix()
    path=run/'report.json'; path.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
    pointer={'path':path.relative_to(args.output).as_posix(),'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}
    temporary=args.output/'latest.tmp'; temporary.write_text(json.dumps(pointer),encoding='utf-8')
    temporary.replace(args.output/'latest.json')
    print(json.dumps({'passed':data['passed'],'cases':cases['case_count'],'suite':args.suite,
                      'report':str(path),'log':str(log)},ensure_ascii=False))
    return 0 if data['passed'] else 1

if __name__=='__main__': raise SystemExit(main())
