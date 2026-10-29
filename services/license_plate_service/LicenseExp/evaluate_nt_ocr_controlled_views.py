#!/usr/bin/env python3
"""Controlled complete-plate NT OCR views and fixed visual fallback fusion."""
from __future__ import annotations
import argparse,csv,json,platform,statistics,sys,time
from pathlib import Path
from typing import Any
import cv2, matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from ultralytics import YOLO
from arabic_marker_ocr import ArabicOCRExperimentError,parse_nt_evidence,sha256_file,unwrap_paddle_result
from evaluate_arabic_marker_ocr import MODEL_NAME,package_version,write_csv
from evaluate_complete_plate_nt_ocr import load_visual_baseline,classification_summary
from plate_preprocessing import ResolutionPolicy,calculate_geometry,resize_and_pad
from plate_variants import validate_manifest

CONDITIONS={
 "A":{"definition":"exact complete_plate_nt_ocr_v1 unpadded budget input; default recognizer shape 3x48x320"},
 "B":{"definition":"best.pt highest confidence >=0.25; accept only >=60% width and >=20% height; 4% source margin; otherwise labelled source fallback"},
 "C":{"definition":"rightmost 40% of B crop, full height, floor(0.60*width) lower x; then budget resize"},
 "D":{"definition":"PP-OCRv5_mobile_det defaults on A; recognize every detected region independently; no joining"},
 "E":{"definition":"same A pixels; documented recognizer input_shape=(3,64,640) instead of default 3x48x320"},
 "ABC_any":{"definition":"NT if any A/B/C is present and none is conflicting; otherwise saved R2"},
}
COLS=("condition","image_id","expected_category","input_path","input_width","input_height","raw_text","native_confidence","normalized_text","nt_evidence","parser_reason","inference_status","failure_reason","localization_status","source_crop_xyxy","baseline_prediction","fused_prediction","correct","override","recognition_seconds")

def args():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--manifest',type=Path,default=Path('all_plate_manifest.json'));p.add_argument('--baseline-dir',type=Path,default=Path('dinov3_s_all12_budget_v1'));p.add_argument('--prior-a-dir',type=Path,default=Path('complete_plate_nt_ocr_v1'));p.add_argument('--weights',type=Path,default=Path('best.pt'));p.add_argument('--output-dir',type=Path,required=True);p.add_argument('--device',default='cpu');return p.parse_args()

def metrics(rows,condition):
 s=[r for r in rows if r['condition']==condition]; done=[r for r in s if r['inference_status']=='completed'];tp=sum(r['expected_category']=='nt' and r['nt_evidence']=='present' for r in done);fp=sum(r['expected_category']!='nt' and r['nt_evidence']=='present' for r in done);fn=sum(r['expected_category']=='nt' and r['nt_evidence']!='present' for r in done);tn=sum(r['expected_category']!='nt' and r['nt_evidence']!='present' for r in done)
 return {'condition':condition,'tp':tp,'fp':fp,'fn':fn,'tn':tn,'precision':None if tp+fp==0 else tp/(tp+fp),'recall':None if tp+fn==0 else tp/(tp+fn),'failures':12-len(done),'fused':classification_summary(s,'fused_prediction'),'corrections':[r['image_id'] for r in s if r['baseline_prediction']!=r['expected_category'] and r['fused_prediction']==r['expected_category']],'regressions':[r['image_id'] for r in s if r['baseline_prediction']==r['expected_category'] and r['fused_prediction']!=r['expected_category']],'mean_recognition_seconds':statistics.mean([float(r['recognition_seconds']) for r in done]) if done else None}

def main():
 a=args()
 if a.output_dir.exists(): raise ArabicOCRExperimentError(f'Output exists: {a.output_dir}')
 recs,rep=validate_manifest(a.manifest)
 if not rep['valid'] or len(recs)!=12: raise ArabicOCRExperimentError('Invalid 12-image manifest')
 baseline,bj=load_visual_baseline(a.baseline_dir,recs);pd=bj['budget_policy'];policy=ResolutionPolicy(pd['target_area'],pd['max_long_edge'],pd['max_patch_count'],tuple(pd['padding_rgb']),pd['interpolation']);ps=(16,16)
 prior=list(csv.DictReader((a.prior_a_dir/'complete_plate_ocr_fusion.csv').open()));prior={r['image_id']:r for r in prior}
 a.output_dir.mkdir(); inp=a.output_dir/'inputs';inp.mkdir(); overlays=a.output_dir/'localization_overlays';overlays.mkdir()
 (a.output_dir/'condition_definitions.json').write_text(json.dumps(CONDITIONS,ensure_ascii=False,indent=2)+'\n')
 detector=YOLO(str(a.weights)); prepared={};locmeta={}
 prep_time=0.0
 for r in recs:
  i=str(r['id']); src=r['image'];h,w=src.shape[:2];t=time.perf_counter(); pred=detector.predict(src,conf=.25,verbose=False)[0]; candidates=[]
  for box,conf in zip(pred.boxes.xyxy.cpu().tolist(),pred.boxes.conf.cpu().tolist()): candidates.append((float(conf),box))
  if candidates:
   conf,box=max(candidates);x1,y1,x2,y2=box; reliable=(x2-x1)>=.60*w and (y2-y1)>=.20*h
  else: conf,box,reliable=None,[0,0,w,h],False
  if reliable:
   mx,my=.04*w,.04*h;x1=max(0,int(x1-mx));y1=max(0,int(y1-my));x2=min(w,int(x2+mx+.999));y2=min(h,int(y2+my+.999));status='accepted'
  else:x1,y1,x2,y2=0,0,w,h;status='fallback_original'
  b=src[y1:y2,x1:x2].copy();cx=int(.60*b.shape[1]);c=b[:,cx:].copy();locmeta[i]={'status':status,'detector_confidence':conf,'crop_xyxy':[x1,y1,x2,y2],'right_xyxy_in_b':[cx,0,b.shape[1],b.shape[0]]}
  show=src.copy();cv2.rectangle(show,(x1,y1),(x2-1,y2-1),(0,255,0) if status=='accepted' else (0,0,255),2);cv2.imwrite(str(overlays/f'{i}.png'),show)
  prepared[i]={}
  ap=Path(prior[i]['submitted_path']);prepared[i]['A']=(cv2.imread(str(ap)),ap,[0,0,w,h],status)
  for key,img,coords in [('B',b,[x1,y1,x2,y2]),('C',c,[x1+cx,y1,x2,y2])]:
   g=calculate_geometry(img.shape,'budget',ps,policy);res,_=resize_and_pad(img,g,policy);p=inp/f'{i}_{key}.png';cv2.imwrite(str(p),res);prepared[i][key]=(res,p,coords,status)
  prep_time+=time.perf_counter()-t
 # contact sheet
 fig,axs=plt.subplots(12,3,figsize=(14,30),squeeze=False)
 for ri,r in enumerate(recs):
  for ci,k in enumerate(('A','B','C')):
   im=prepared[str(r['id'])][k][0];axs[ri,ci].imshow(cv2.cvtColor(im,cv2.COLOR_BGR2RGB));axs[ri,ci].set_title(f"{r['id']} {r['category']} {k} {im.shape[1]}x{im.shape[0]} {locmeta[str(r['id'])]['status'] if k!='A' else ''}");axs[ri,ci].axis('off')
 fig.tight_layout();fig.savefig(a.output_dir/'abc_contact_sheet.png',dpi=140);plt.close(fig)
 from paddleocr import TextRecognition
 t=time.perf_counter();m=TextRecognition(model_name=MODEL_NAME,device=a.device);load=time.perf_counter()-t;t=time.perf_counter();list(m.predict(input=str(prepared['1']['A'][1]),batch_size=1));warm=time.perf_counter()-t
 t=time.perf_counter();me=TextRecognition(model_name=MODEL_NAME,device=a.device,input_shape=(3,64,640));eload=time.perf_counter()-t;t=time.perf_counter();list(me.predict(input=str(prepared['1']['A'][1]),batch_size=1));ewarm=time.perf_counter()-t
 rows=[];native=[]
 for cond,model in [('A',m),('B',m),('C',m),('E',me)]:
  for r in recs:
   i=str(r['id']);view='A' if cond=='E' else cond;im,p,coords,ls=prepared[i][view];t=time.perf_counter();out=list(model.predict(input=str(p),batch_size=1))[0];elapsed=time.perf_counter()-t;raw,conf,payload=unwrap_paddle_result(out);pa=parse_nt_evidence(raw);base=baseline[i]['predicted_category'];fused='nt' if pa['decision']=='present' else base
   rows.append({'condition':cond,'image_id':i,'expected_category':r['category'],'input_path':str(p),'input_width':im.shape[1],'input_height':im.shape[0],'raw_text':raw,'native_confidence':conf,'normalized_text':pa['normalized_text'],'nt_evidence':pa['decision'],'parser_reason':pa['reason'],'inference_status':'completed','failure_reason':'','localization_status':ls if cond in ('B','C') else 'not_applicable','source_crop_xyxy':json.dumps(coords),'baseline_prediction':base,'fused_prediction':fused,'correct':fused==r['category'],'override':fused!=base,'recognition_seconds':elapsed});native.append({'condition':cond,'image_id':i,'payload':payload})
 # D blocked: detector absent and official hosts unavailable
 blocker='PP-OCRv5_mobile_det is not cached; PaddleX reported no reachable official model host, so no regions were fabricated.'
 for r in recs:
  i=str(r['id']);base=baseline[i]['predicted_category'];im,p,coords,ls=prepared[i]['A'];rows.append({'condition':'D','image_id':i,'expected_category':r['category'],'input_path':str(p),'input_width':im.shape[1],'input_height':im.shape[0],'raw_text':'','native_confidence':'','normalized_text':'','nt_evidence':'unavailable','parser_reason':'detector_blocked','inference_status':'blocked','failure_reason':blocker,'localization_status':'blocked','source_crop_xyxy':json.dumps(coords),'baseline_prediction':base,'fused_prediction':base,'correct':base==r['category'],'override':False,'recognition_seconds':0.0})
 # fixed multi-view
 for r in recs:
  i=str(r['id']);views=[x for x in rows if x['image_id']==i and x['condition'] in ('A','B','C')];contrib=[x['condition'] for x in views if x['nt_evidence']=='present'];conflict=any(x['nt_evidence']=='conflicting' for x in views);base=baseline[i]['predicted_category'];fused='nt' if contrib and not conflict else base
  rows.append({'condition':'ABC_any','image_id':i,'expected_category':r['category'],'input_path':'A;B;C','input_width':'','input_height':'','raw_text':' | '.join(f"{x['condition']}:{x['raw_text']}" for x in views),'native_confidence':'','normalized_text':'','nt_evidence':'conflicting' if conflict else ('present' if contrib else 'absent'),'parser_reason':'contributors='+','.join(contrib),'inference_status':'completed','failure_reason':'','localization_status':locmeta[i]['status'],'source_crop_xyxy':'','baseline_prediction':base,'fused_prediction':fused,'correct':fused==r['category'],'override':fused!=base,'recognition_seconds':sum(float(x['recognition_seconds']) for x in views)})
 # A reproduction
 discrepancies=[]
 for r in [x for x in rows if x['condition']=='A']:
  if r['raw_text']!=prior[r['image_id']]['raw_text'] or r['nt_evidence']!=prior[r['image_id']]['nt_evidence']: discrepancies.append(r['image_id'])
 sums=[metrics(rows,c) for c in CONDITIONS]
 write_csv(a.output_dir/'results.csv',rows,COLS);(a.output_dir/'results.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2)+'\n');(a.output_dir/'summary_metrics.json').write_text(json.dumps(sums,ensure_ascii=False,indent=2)+'\n');write_csv(a.output_dir/'summary_metrics.csv',[{'condition':s['condition'],'tp':s['tp'],'fp':s['fp'],'fn':s['fn'],'tn':s['tn'],'precision':s['precision'],'recall':s['recall'],'failures':s['failures'],'fused_correct':s['fused']['correct'],'fused_total':s['fused']['total'],'corrections':';'.join(s['corrections']),'regressions':';'.join(s['regressions']),'mean_recognition_seconds':s['mean_recognition_seconds']} for s in sums],('condition','tp','fp','fn','tn','precision','recall','failures','fused_correct','fused_total','corrections','regressions','mean_recognition_seconds'));(a.output_dir/'native_outputs.json').write_text(json.dumps(native,ensure_ascii=False,indent=2)+'\n')
 config={'conditions':CONDITIONS,'manifest':str(a.manifest),'baseline':str(a.baseline_dir),'prior_A':str(a.prior_a_dir),'A_discrepancies':discrepancies,'localization':locmeta,'weights_sha256':sha256_file(a.weights),'recognizer':MODEL_NAME,'default_input_shape':[3,48,320],'E_input_shape_requested_and_runtime_confirmed':[3,64,640],'D_status':'blocked','D_blocker':blocker,'versions':{x:package_version(x) for x in ('paddleocr','paddlepaddle','opencv-python','numpy','ultralytics')},'device':a.device,'timing':{'recognizer_load':load,'warmup':warm,'E_load':eload,'E_warmup':ewarm,'ABC_preprocessing_and_localization':prep_time}}
 (a.output_dir/'configuration.json').write_text(json.dumps(config,ensure_ascii=False,indent=2)+'\n')
 lines=['# Controlled NT OCR Views','',f'A reproduction discrepancies: {discrepancies or "none"}.',f'D blocked: {blocker}','', '| Condition | TP | FP | FN | TN | Precision | Recall | Fused | Corrections | Regressions |','|---|---:|---:|---:|---:|---:|---:|---:|---|---|']
 for s in sums:lines.append(f"| {s['condition']} | {s['tp']} | {s['fp']} | {s['fn']} | {s['tn']} | {s['precision']} | {s['recall']} | {s['fused']['correct']}/12 | {s['corrections']} | {s['regressions']} |")
 lines+=['','## Per image','','| ID | Expected | A | B | C | E | Multi |','|---:|---|---|---|---|---|---|']
 for r in recs:
  i=str(r['id']);by={x['condition']:x for x in rows if x['image_id']==i};fmt=lambda c:f"`{by[c]['raw_text']}` / {by[c]['nt_evidence']}";lines.append(f"| {i} | {r['category']} | {fmt('A')} | {fmt('B')} | {fmt('C')} | {fmt('E')} | {by['ABC_any']['fused_prediction']} |")
 lines+=['','D has no fabricated output. These repeatedly used development images do not establish generalization.']
 (a.output_dir/'REPORT.md').write_text('\n'.join(lines)+'\n');print(a.output_dir.resolve())
if __name__=='__main__':
 try:main()
 except ArabicOCRExperimentError as e:print(f'Error: {e}',file=sys.stderr);raise SystemExit(1)
