# Whole-plate recognition file inventory

Paths are absolute. Modified files are compared with the pre-task snapshot, not Git HEAD. Generated evidence is included. Unrelated working-tree changes are preserved.

| Status | Full path | Purpose |
|---|---|---|
| Modified | `/home/intern/part-time/mohamed/pipeline/README.md` | Current application-format capability and configuration documentation. |
| Modified | `/home/intern/part-time/mohamed/pipeline/services/license_plate_service/run_rtsp_pipeline/ocr.py` | Retain native OCR score alongside legacy rounded confidence. |
| Modified | `/home/intern/part-time/mohamed/pipeline/services/license_plate_service/run_rtsp_pipeline/pipeline.py` | Dispatch new policies before private marker logic; preserve category-specific errors. |
| Modified | `/home/intern/part-time/mohamed/pipeline/services/license_plate_service/run_rtsp_pipeline/prototype_pipeline.py` | Use OCR score for new-policy compatibility confidence without requiring marker score. |
| Modified | `/home/intern/part-time/mohamed/pipeline/services/license_plate_service/run_video_pipeline/config.py` | Configurable WHOLE_PLATE_MIN_OCR_CONF, initial 0.90. |
| Modified | `/home/intern/part-time/mohamed/pipeline/services/license_plate_service/run_video_pipeline/ocr.py` | Retain native OCR score alongside legacy rounded confidence. |
| Modified | `/home/intern/part-time/mohamed/pipeline/services/license_plate_service/run_video_pipeline/pipeline.py` | Dispatch new policies before private marker logic; preserve category-specific errors. |
| Modified | `/home/intern/part-time/mohamed/pipeline/services/license_plate_service/run_video_pipeline/policies.py` | Enable three real number policies; preserve Tunis validator. |
| Modified | `/home/intern/part-time/mohamed/pipeline/services/license_plate_service/run_video_pipeline/prototype_pipeline.py` | Use OCR score for new-policy compatibility confidence without requiring marker score. |
| Modified | `/home/intern/part-time/mohamed/pipeline/services/license_plate_service/tests/test_classifier_runtime.py` | Replace obsolete disabled-policy test with whole-crop dispatch contract. |
| Modified | `/home/intern/part-time/mohamed/pipeline/vehicle/license_plate_service/run_rtsp_pipeline/ocr.py` | Retain native OCR score alongside legacy rounded confidence. |
| Modified | `/home/intern/part-time/mohamed/pipeline/vehicle/license_plate_service/run_rtsp_pipeline/pipeline.py` | Dispatch new policies before private marker logic; preserve category-specific errors. |
| Modified | `/home/intern/part-time/mohamed/pipeline/vehicle/license_plate_service/run_rtsp_pipeline/prototype_pipeline.py` | Use OCR score for new-policy compatibility confidence without requiring marker score. |
| Modified | `/home/intern/part-time/mohamed/pipeline/vehicle/license_plate_service/run_video_pipeline/config.py` | Configurable WHOLE_PLATE_MIN_OCR_CONF, initial 0.90. |
| Modified | `/home/intern/part-time/mohamed/pipeline/vehicle/license_plate_service/run_video_pipeline/ocr.py` | Retain native OCR score alongside legacy rounded confidence. |
| Modified | `/home/intern/part-time/mohamed/pipeline/vehicle/license_plate_service/run_video_pipeline/pipeline.py` | Dispatch new policies before private marker logic; preserve category-specific errors. |
| Modified | `/home/intern/part-time/mohamed/pipeline/vehicle/license_plate_service/run_video_pipeline/policies.py` | Enable three real number policies; preserve Tunis validator. |
| Modified | `/home/intern/part-time/mohamed/pipeline/vehicle/license_plate_service/run_video_pipeline/prototype_pipeline.py` | Use OCR score for new-policy compatibility confidence without requiring marker score. |
| Modified | `/home/intern/part-time/mohamed/pipeline/vehicle/license_plate_service/tests.py` | Backend API formatting and rejected-number suppression using mocked inference. |
| Added | `/home/intern/part-time/mohamed/pipeline/docs/whole_plate_number_inventory.md` | Every task file listed with full path and purpose. |
| Added | `/home/intern/part-time/mohamed/pipeline/docs/whole_plate_number_policies.md` | Policies, evidence, results, limitations and exact reproduction commands. |
| Added | `/home/intern/part-time/mohamed/pipeline/docs/whole_plate_number_policies.patch` | Complete task-specific text and binary patch; excluded from its own contents. |
| Added | `/home/intern/part-time/mohamed/pipeline/experiments/whole_plate_policies/checks/backend.log` | Saved check/inference output or structural verification evidence. |
| Added | `/home/intern/part-time/mohamed/pipeline/experiments/whole_plate_policies/checks/frontend.txt` | Saved check/inference output or structural verification evidence. |
| Added | `/home/intern/part-time/mohamed/pipeline/experiments/whole_plate_policies/checks/pipeline_django.log` | Saved check/inference output or structural verification evidence. |
| Added | `/home/intern/part-time/mohamed/pipeline/experiments/whole_plate_policies/checks/pipeline_standalone.log` | Saved check/inference output or structural verification evidence. |
| Added | `/home/intern/part-time/mohamed/pipeline/experiments/whole_plate_policies/checks/plugin_imports.log` | Saved check/inference output or structural verification evidence. |
| Added | `/home/intern/part-time/mohamed/pipeline/experiments/whole_plate_policies/checks/real_django.log` | Saved check/inference output or structural verification evidence. |
| Added | `/home/intern/part-time/mohamed/pipeline/experiments/whole_plate_policies/checks/real_parity.txt` | Saved check/inference output or structural verification evidence. |
| Added | `/home/intern/part-time/mohamed/pipeline/experiments/whole_plate_policies/checks/real_standalone.log` | Saved check/inference output or structural verification evidence. |
| Added | `/home/intern/part-time/mohamed/pipeline/experiments/whole_plate_policies/checks/structural_checks.txt` | Saved check/inference output or structural verification evidence. |
| Added | `/home/intern/part-time/mohamed/pipeline/experiments/whole_plate_policies/checks/upload_smoke.log` | Saved check/inference output or structural verification evidence. |
| Added | `/home/intern/part-time/mohamed/pipeline/experiments/whole_plate_policies/checks/video_smoke.log` | Saved check/inference output or structural verification evidence. |
| Added | `/home/intern/part-time/mohamed/pipeline/experiments/whole_plate_policies/django_final/REPORT.md` | Per-reference actual OCR results and manifest comparisons, separate from inference. |
| Added | `/home/intern/part-time/mohamed/pipeline/experiments/whole_plate_policies/django_final/captures/crops/389191d2-5c82-462c-877e-f34c26f7f777.jpg` | Unmodified source/reference crop or persisted actual inference crop. |
| Added | `/home/intern/part-time/mohamed/pipeline/experiments/whole_plate_policies/django_final/captures/crops/4a97f010-7f84-4dd2-8ade-b2c261d98c40.jpg` | Unmodified source/reference crop or persisted actual inference crop. |
| Added | `/home/intern/part-time/mohamed/pipeline/experiments/whole_plate_policies/django_final/captures/crops/5795156b-f73d-45f7-8d40-fc18d73085e9.jpg` | Unmodified source/reference crop or persisted actual inference crop. |
| Added | `/home/intern/part-time/mohamed/pipeline/experiments/whole_plate_policies/django_final/captures/crops/80f7895b-05ec-4e17-8ca6-6f3824dee9d3.jpg` | Unmodified source/reference crop or persisted actual inference crop. |
| Added | `/home/intern/part-time/mohamed/pipeline/experiments/whole_plate_policies/django_final/captures/crops/8cd1a705-3a3f-44f8-9db6-89617c9fd4aa.jpg` | Unmodified source/reference crop or persisted actual inference crop. |
| Added | `/home/intern/part-time/mohamed/pipeline/experiments/whole_plate_policies/django_final/captures/crops/a4420e43-adbb-4e72-b33e-71aadcd9be76.jpg` | Unmodified source/reference crop or persisted actual inference crop. |
| Added | `/home/intern/part-time/mohamed/pipeline/experiments/whole_plate_policies/django_final/captures/crops/be22858a-2e2b-4166-add6-0beda465880b.jpg` | Unmodified source/reference crop or persisted actual inference crop. |
| Added | `/home/intern/part-time/mohamed/pipeline/experiments/whole_plate_policies/django_final/captures/crops/cd29014c-8e9d-4552-bb2f-e5b4db5ad8e8.jpg` | Unmodified source/reference crop or persisted actual inference crop. |
| Added | `/home/intern/part-time/mohamed/pipeline/experiments/whole_plate_policies/django_final/captures/crops/dc6cd8c0-d8f3-419d-899e-bd4f6f8de49e.jpg` | Unmodified source/reference crop or persisted actual inference crop. |
| Added | `/home/intern/part-time/mohamed/pipeline/experiments/whole_plate_policies/django_final/captures/crops/ecbb8809-8598-4942-9142-45acfef3ae04.jpg` | Unmodified source/reference crop or persisted actual inference crop. |
| Added | `/home/intern/part-time/mohamed/pipeline/experiments/whole_plate_policies/django_final/captures/crops/f6320229-4cd0-4bd5-86e6-cb3cfeed0d3f.jpg` | Unmodified source/reference crop or persisted actual inference crop. |
| Added | `/home/intern/part-time/mohamed/pipeline/experiments/whole_plate_policies/django_final/captures/crops/fae33420-8d37-4d4c-b297-a8a554eadc6b.jpg` | Unmodified source/reference crop or persisted actual inference crop. |
| Added | `/home/intern/part-time/mohamed/pipeline/experiments/whole_plate_policies/django_final/captures/prediction_debug.jsonl` | Actual compact prediction or raw/debug number evidence from inference. |
| Added | `/home/intern/part-time/mohamed/pipeline/experiments/whole_plate_policies/django_final/captures/predictions.jsonl` | Actual compact prediction or raw/debug number evidence from inference. |
| Added | `/home/intern/part-time/mohamed/pipeline/experiments/whole_plate_policies/django_final/comparison.csv` | Per-reference actual OCR results and manifest comparisons, separate from inference. |
| Added | `/home/intern/part-time/mohamed/pipeline/experiments/whole_plate_policies/django_final/contact_sheet.png` | Original/reference preview with result captions; inspected visually. |
| Added | `/home/intern/part-time/mohamed/pipeline/experiments/whole_plate_policies/django_final/original_1.png` | Unmodified source/reference crop or persisted actual inference crop. |
| Added | `/home/intern/part-time/mohamed/pipeline/experiments/whole_plate_policies/django_final/original_10.png` | Unmodified source/reference crop or persisted actual inference crop. |
| Added | `/home/intern/part-time/mohamed/pipeline/experiments/whole_plate_policies/django_final/original_11.png` | Unmodified source/reference crop or persisted actual inference crop. |
| Added | `/home/intern/part-time/mohamed/pipeline/experiments/whole_plate_policies/django_final/original_12.png` | Unmodified source/reference crop or persisted actual inference crop. |
| Added | `/home/intern/part-time/mohamed/pipeline/experiments/whole_plate_policies/django_final/original_2.png` | Unmodified source/reference crop or persisted actual inference crop. |
| Added | `/home/intern/part-time/mohamed/pipeline/experiments/whole_plate_policies/django_final/original_3.png` | Unmodified source/reference crop or persisted actual inference crop. |
| Added | `/home/intern/part-time/mohamed/pipeline/experiments/whole_plate_policies/django_final/original_4.png` | Unmodified source/reference crop or persisted actual inference crop. |
| Added | `/home/intern/part-time/mohamed/pipeline/experiments/whole_plate_policies/django_final/original_5.png` | Unmodified source/reference crop or persisted actual inference crop. |
| Added | `/home/intern/part-time/mohamed/pipeline/experiments/whole_plate_policies/django_final/original_6.png` | Unmodified source/reference crop or persisted actual inference crop. |
| Added | `/home/intern/part-time/mohamed/pipeline/experiments/whole_plate_policies/django_final/original_7.png` | Unmodified source/reference crop or persisted actual inference crop. |
| Added | `/home/intern/part-time/mohamed/pipeline/experiments/whole_plate_policies/django_final/original_8.png` | Unmodified source/reference crop or persisted actual inference crop. |
| Added | `/home/intern/part-time/mohamed/pipeline/experiments/whole_plate_policies/django_final/original_9.png` | Unmodified source/reference crop or persisted actual inference crop. |
| Added | `/home/intern/part-time/mohamed/pipeline/experiments/whole_plate_policies/django_final/preview_1.png` | Original/reference preview with result captions; inspected visually. |
| Added | `/home/intern/part-time/mohamed/pipeline/experiments/whole_plate_policies/django_final/preview_10.png` | Original/reference preview with result captions; inspected visually. |
| Added | `/home/intern/part-time/mohamed/pipeline/experiments/whole_plate_policies/django_final/preview_11.png` | Original/reference preview with result captions; inspected visually. |
| Added | `/home/intern/part-time/mohamed/pipeline/experiments/whole_plate_policies/django_final/preview_12.png` | Original/reference preview with result captions; inspected visually. |
| Added | `/home/intern/part-time/mohamed/pipeline/experiments/whole_plate_policies/django_final/preview_2.png` | Original/reference preview with result captions; inspected visually. |
| Added | `/home/intern/part-time/mohamed/pipeline/experiments/whole_plate_policies/django_final/preview_3.png` | Original/reference preview with result captions; inspected visually. |
| Added | `/home/intern/part-time/mohamed/pipeline/experiments/whole_plate_policies/django_final/preview_4.png` | Original/reference preview with result captions; inspected visually. |
| Added | `/home/intern/part-time/mohamed/pipeline/experiments/whole_plate_policies/django_final/preview_5.png` | Original/reference preview with result captions; inspected visually. |
| Added | `/home/intern/part-time/mohamed/pipeline/experiments/whole_plate_policies/django_final/preview_6.png` | Original/reference preview with result captions; inspected visually. |
| Added | `/home/intern/part-time/mohamed/pipeline/experiments/whole_plate_policies/django_final/preview_7.png` | Original/reference preview with result captions; inspected visually. |
| Added | `/home/intern/part-time/mohamed/pipeline/experiments/whole_plate_policies/django_final/preview_8.png` | Original/reference preview with result captions; inspected visually. |
| Added | `/home/intern/part-time/mohamed/pipeline/experiments/whole_plate_policies/django_final/preview_9.png` | Original/reference preview with result captions; inspected visually. |
| Added | `/home/intern/part-time/mohamed/pipeline/experiments/whole_plate_policies/django_final/results.json` | Per-reference actual OCR results and manifest comparisons, separate from inference. |
| Added | `/home/intern/part-time/mohamed/pipeline/experiments/whole_plate_policies/original_contact_sheet.png` | Unmodified source/reference crop or persisted actual inference crop. |
| Added | `/home/intern/part-time/mohamed/pipeline/experiments/whole_plate_policies/standalone_final/REPORT.md` | Per-reference actual OCR results and manifest comparisons, separate from inference. |
| Added | `/home/intern/part-time/mohamed/pipeline/experiments/whole_plate_policies/standalone_final/captures/crops/0f835dd3-f988-4a43-bb7e-e7605bdbe7ef.jpg` | Unmodified source/reference crop or persisted actual inference crop. |
| Added | `/home/intern/part-time/mohamed/pipeline/experiments/whole_plate_policies/standalone_final/captures/crops/0ff27205-2498-4f8c-9c76-4727c83c9176.jpg` | Unmodified source/reference crop or persisted actual inference crop. |
| Added | `/home/intern/part-time/mohamed/pipeline/experiments/whole_plate_policies/standalone_final/captures/crops/475da39a-e06e-4d3c-8be2-6c769713d1f0.jpg` | Unmodified source/reference crop or persisted actual inference crop. |
| Added | `/home/intern/part-time/mohamed/pipeline/experiments/whole_plate_policies/standalone_final/captures/crops/5b5ad180-64d5-4996-9b84-a28f3eec22e3.jpg` | Unmodified source/reference crop or persisted actual inference crop. |
| Added | `/home/intern/part-time/mohamed/pipeline/experiments/whole_plate_policies/standalone_final/captures/crops/9922dbc4-1767-4336-87cc-604696f67fce.jpg` | Unmodified source/reference crop or persisted actual inference crop. |
| Added | `/home/intern/part-time/mohamed/pipeline/experiments/whole_plate_policies/standalone_final/captures/crops/9afd4186-3a83-4c22-8b47-9d416351fa70.jpg` | Unmodified source/reference crop or persisted actual inference crop. |
| Added | `/home/intern/part-time/mohamed/pipeline/experiments/whole_plate_policies/standalone_final/captures/crops/bae3fb83-a57f-4031-b7f4-86af4b504dd0.jpg` | Unmodified source/reference crop or persisted actual inference crop. |
| Added | `/home/intern/part-time/mohamed/pipeline/experiments/whole_plate_policies/standalone_final/captures/crops/cead8cf9-04f4-4222-8d97-e583213b2eed.jpg` | Unmodified source/reference crop or persisted actual inference crop. |
| Added | `/home/intern/part-time/mohamed/pipeline/experiments/whole_plate_policies/standalone_final/captures/crops/d3718289-6822-49b8-851d-b27eb6fbd23d.jpg` | Unmodified source/reference crop or persisted actual inference crop. |
| Added | `/home/intern/part-time/mohamed/pipeline/experiments/whole_plate_policies/standalone_final/captures/crops/e78b28ae-57c5-451a-8d15-1539f3f6c1f9.jpg` | Unmodified source/reference crop or persisted actual inference crop. |
| Added | `/home/intern/part-time/mohamed/pipeline/experiments/whole_plate_policies/standalone_final/captures/crops/f58a83fa-69e8-4f40-b5f8-64b5f5a8b0a2.jpg` | Unmodified source/reference crop or persisted actual inference crop. |
| Added | `/home/intern/part-time/mohamed/pipeline/experiments/whole_plate_policies/standalone_final/captures/crops/f5b97d22-ae33-4bd0-9e3b-c099200106b5.jpg` | Unmodified source/reference crop or persisted actual inference crop. |
| Added | `/home/intern/part-time/mohamed/pipeline/experiments/whole_plate_policies/standalone_final/captures/prediction_debug.jsonl` | Actual compact prediction or raw/debug number evidence from inference. |
| Added | `/home/intern/part-time/mohamed/pipeline/experiments/whole_plate_policies/standalone_final/captures/predictions.jsonl` | Actual compact prediction or raw/debug number evidence from inference. |
| Added | `/home/intern/part-time/mohamed/pipeline/experiments/whole_plate_policies/standalone_final/comparison.csv` | Per-reference actual OCR results and manifest comparisons, separate from inference. |
| Added | `/home/intern/part-time/mohamed/pipeline/experiments/whole_plate_policies/standalone_final/contact_sheet.png` | Original/reference preview with result captions; inspected visually. |
| Added | `/home/intern/part-time/mohamed/pipeline/experiments/whole_plate_policies/standalone_final/original_1.png` | Unmodified source/reference crop or persisted actual inference crop. |
| Added | `/home/intern/part-time/mohamed/pipeline/experiments/whole_plate_policies/standalone_final/original_10.png` | Unmodified source/reference crop or persisted actual inference crop. |
| Added | `/home/intern/part-time/mohamed/pipeline/experiments/whole_plate_policies/standalone_final/original_11.png` | Unmodified source/reference crop or persisted actual inference crop. |
| Added | `/home/intern/part-time/mohamed/pipeline/experiments/whole_plate_policies/standalone_final/original_12.png` | Unmodified source/reference crop or persisted actual inference crop. |
| Added | `/home/intern/part-time/mohamed/pipeline/experiments/whole_plate_policies/standalone_final/original_2.png` | Unmodified source/reference crop or persisted actual inference crop. |
| Added | `/home/intern/part-time/mohamed/pipeline/experiments/whole_plate_policies/standalone_final/original_3.png` | Unmodified source/reference crop or persisted actual inference crop. |
| Added | `/home/intern/part-time/mohamed/pipeline/experiments/whole_plate_policies/standalone_final/original_4.png` | Unmodified source/reference crop or persisted actual inference crop. |
| Added | `/home/intern/part-time/mohamed/pipeline/experiments/whole_plate_policies/standalone_final/original_5.png` | Unmodified source/reference crop or persisted actual inference crop. |
| Added | `/home/intern/part-time/mohamed/pipeline/experiments/whole_plate_policies/standalone_final/original_6.png` | Unmodified source/reference crop or persisted actual inference crop. |
| Added | `/home/intern/part-time/mohamed/pipeline/experiments/whole_plate_policies/standalone_final/original_7.png` | Unmodified source/reference crop or persisted actual inference crop. |
| Added | `/home/intern/part-time/mohamed/pipeline/experiments/whole_plate_policies/standalone_final/original_8.png` | Unmodified source/reference crop or persisted actual inference crop. |
| Added | `/home/intern/part-time/mohamed/pipeline/experiments/whole_plate_policies/standalone_final/original_9.png` | Unmodified source/reference crop or persisted actual inference crop. |
| Added | `/home/intern/part-time/mohamed/pipeline/experiments/whole_plate_policies/standalone_final/preview_1.png` | Original/reference preview with result captions; inspected visually. |
| Added | `/home/intern/part-time/mohamed/pipeline/experiments/whole_plate_policies/standalone_final/preview_10.png` | Original/reference preview with result captions; inspected visually. |
| Added | `/home/intern/part-time/mohamed/pipeline/experiments/whole_plate_policies/standalone_final/preview_11.png` | Original/reference preview with result captions; inspected visually. |
| Added | `/home/intern/part-time/mohamed/pipeline/experiments/whole_plate_policies/standalone_final/preview_12.png` | Original/reference preview with result captions; inspected visually. |
| Added | `/home/intern/part-time/mohamed/pipeline/experiments/whole_plate_policies/standalone_final/preview_2.png` | Original/reference preview with result captions; inspected visually. |
| Added | `/home/intern/part-time/mohamed/pipeline/experiments/whole_plate_policies/standalone_final/preview_3.png` | Original/reference preview with result captions; inspected visually. |
| Added | `/home/intern/part-time/mohamed/pipeline/experiments/whole_plate_policies/standalone_final/preview_4.png` | Original/reference preview with result captions; inspected visually. |
| Added | `/home/intern/part-time/mohamed/pipeline/experiments/whole_plate_policies/standalone_final/preview_5.png` | Original/reference preview with result captions; inspected visually. |
| Added | `/home/intern/part-time/mohamed/pipeline/experiments/whole_plate_policies/standalone_final/preview_6.png` | Original/reference preview with result captions; inspected visually. |
| Added | `/home/intern/part-time/mohamed/pipeline/experiments/whole_plate_policies/standalone_final/preview_7.png` | Original/reference preview with result captions; inspected visually. |
| Added | `/home/intern/part-time/mohamed/pipeline/experiments/whole_plate_policies/standalone_final/preview_8.png` | Original/reference preview with result captions; inspected visually. |
| Added | `/home/intern/part-time/mohamed/pipeline/experiments/whole_plate_policies/standalone_final/preview_9.png` | Original/reference preview with result captions; inspected visually. |
| Added | `/home/intern/part-time/mohamed/pipeline/experiments/whole_plate_policies/standalone_final/results.json` | Per-reference actual OCR results and manifest comparisons, separate from inference. |
| Added | `/home/intern/part-time/mohamed/pipeline/experiments/whole_plate_policies/upload_smoke/media/license_plate/6a6500c6a63f4000b4b349fbf8752773/annotated.mp4` | Real video smoke-check input or annotated playable output. |
| Added | `/home/intern/part-time/mohamed/pipeline/experiments/whole_plate_policies/upload_smoke/media/license_plate/6a6500c6a63f4000b4b349fbf8752773/captures/crops/f71b4a27-3fd5-4647-85e1-09e891dffc20.jpg` | Unmodified source/reference crop or persisted actual inference crop. |
| Added | `/home/intern/part-time/mohamed/pipeline/experiments/whole_plate_policies/upload_smoke/media/license_plate/6a6500c6a63f4000b4b349fbf8752773/captures/prediction_debug.jsonl` | Actual compact prediction or raw/debug number evidence from inference. |
| Added | `/home/intern/part-time/mohamed/pipeline/experiments/whole_plate_policies/upload_smoke/media/license_plate/6a6500c6a63f4000b4b349fbf8752773/captures/predictions.jsonl` | Actual compact prediction or raw/debug number evidence from inference. |
| Added | `/home/intern/part-time/mohamed/pipeline/experiments/whole_plate_policies/upload_smoke/media/license_plate/6a6500c6a63f4000b4b349fbf8752773/selected_crops/track_1_early.jpg` | Generated task verification artifact. |
| Added | `/home/intern/part-time/mohamed/pipeline/experiments/whole_plate_policies/upload_smoke/media/license_plate/6a6500c6a63f4000b4b349fbf8752773/selected_crops/track_1_final_best.jpg` | Generated task verification artifact. |
| Added | `/home/intern/part-time/mohamed/pipeline/experiments/whole_plate_policies/upload_smoke/media/license_plate/6a6500c6a63f4000b4b349fbf8752773/selected_crops/track_1_recognized.jpg` | Generated task verification artifact. |
| Added | `/home/intern/part-time/mohamed/pipeline/experiments/whole_plate_policies/upload_smoke/media/license_plate/6a6500c6a63f4000b4b349fbf8752773/source.mp4` | Real video smoke-check input or annotated playable output. |
| Added | `/home/intern/part-time/mohamed/pipeline/experiments/whole_plate_policies/upload_smoke/verification.json` | Real end-to-end video/upload result, metrics and network-attempt count. |
| Added | `/home/intern/part-time/mohamed/pipeline/experiments/whole_plate_policies/verify.py` | Reproducible real worker/classifier/registration OCR check and preview generation. |
| Added | `/home/intern/part-time/mohamed/pipeline/experiments/whole_plate_policies/video_smoke/annotated.mp4` | Real video smoke-check input or annotated playable output. |
| Added | `/home/intern/part-time/mohamed/pipeline/experiments/whole_plate_policies/video_smoke/captures/crops/46603081-ad35-452d-88a2-8896b4ab5baa.jpg` | Unmodified source/reference crop or persisted actual inference crop. |
| Added | `/home/intern/part-time/mohamed/pipeline/experiments/whole_plate_policies/video_smoke/captures/prediction_debug.jsonl` | Actual compact prediction or raw/debug number evidence from inference. |
| Added | `/home/intern/part-time/mohamed/pipeline/experiments/whole_plate_policies/video_smoke/captures/predictions.jsonl` | Actual compact prediction or raw/debug number evidence from inference. |
| Added | `/home/intern/part-time/mohamed/pipeline/experiments/whole_plate_policies/video_smoke/metrics.json` | Generated task verification artifact. |
| Added | `/home/intern/part-time/mohamed/pipeline/experiments/whole_plate_policies/video_smoke/metrics.tracks.json` | Generated task verification artifact. |
| Added | `/home/intern/part-time/mohamed/pipeline/experiments/whole_plate_policies/video_smoke/selected_crops/track_1_early.jpg` | Generated task verification artifact. |
| Added | `/home/intern/part-time/mohamed/pipeline/experiments/whole_plate_policies/video_smoke/selected_crops/track_1_final_best.jpg` | Generated task verification artifact. |
| Added | `/home/intern/part-time/mohamed/pipeline/experiments/whole_plate_policies/video_smoke/selected_crops/track_1_recognized.jpg` | Generated task verification artifact. |
| Added | `/home/intern/part-time/mohamed/pipeline/experiments/whole_plate_policies/video_smoke/verification.json` | Real end-to-end video/upload result, metrics and network-attempt count. |
| Added | `/home/intern/part-time/mohamed/pipeline/services/license_plate_service/run_video_pipeline/whole_plate_numbers.py` | Conservative whole-plate extraction, user templates, threshold and contradiction checks. |
| Added | `/home/intern/part-time/mohamed/pipeline/services/license_plate_service/tests/test_whole_plate_numbers.py` | Extraction, acceptance, persistence, exports, annotations, RTSP, identity and Tunis regressions. |
| Added | `/home/intern/part-time/mohamed/pipeline/vehicle/license_plate_service/run_video_pipeline/whole_plate_numbers.py` | Conservative whole-plate extraction, user templates, threshold and contradiction checks. |
