# DINOv3 Plate Representation Comparison

## Execution status

DINOv2-small completed for native and budget rectangular inputs. Both requested DINOv3 models are blocked before loading because the official Meta repository and exact checkpoints are absent. No Hugging Face substitute or fabricated result was used.

The historical ten source identities were recovered after file renaming and verified by SHA-256. Newly added NT files were not substituted.

## Completed classification results

| Encoder | Mode | Subset | R0 | R1 | R2 | R3 |
|---|---|---|---:|---:|---:|---:|
| DINOv2-small | budget | full_10 | 5/10 | 5/10 | 7/10 | — |
| DINOv2-small | budget | marker_7 | 4/7 | 4/7 | 5/7 | 4/7 |
| DINOv2-small | native | full_10 | 3/10 | 4/10 | 4/10 | — |
| DINOv2-small | native | marker_7 | 2/7 | 2/7 | 2/7 | 2/7 |

Budget R0 reproduces the authoritative baseline: maximum matrix difference 3.58e-7 and identical nearest-neighbour predictions. Native inputs do not improve these examples.

## NT and Shorta

- budget r0_cls: 4 nt→idara (wrong), 5 nt→tunis (wrong), 6 shorta→shorta (correct), 7 shorta→shorta (correct)
- budget r1_mean_patch: 4 nt→idara (wrong), 5 nt→tunis (wrong), 6 shorta→shorta (correct), 7 shorta→shorta (correct)
- budget r2_global_regional: 4 nt→idara (wrong), 5 nt→tunis (wrong), 6 shorta→shorta (correct), 7 shorta→shorta (correct)
- native r0_cls: 4 nt→idara (wrong), 5 nt→tunis (wrong), 6 shorta→tunis (wrong), 7 shorta→shorta (correct)
- native r1_mean_patch: 4 nt→idara (wrong), 5 nt→tunis (wrong), 6 shorta→shorta (correct), 7 shorta→shorta (correct)
- native r2_global_regional: 4 nt→idara (wrong), 5 nt→tunis (wrong), 6 shorta→nt (wrong), 7 shorta→shorta (correct)

## Timing and cost

- budget: load 0.370s; mean whole pass 0.0764s; mean regional pass 0.0756s; mean marker pass 0.0757s; R2 total for ten plates 3.033s (CPU).
- native: load 0.366s; mean whole pass 0.1060s; mean regional pass 0.0537s; mean marker pass 0.0130s; R2 total for ten plates 2.670s (CPU).

R0 and R1 share one whole-image forward; R2 uses four views per plate; R3 uses one marker view for each eligible plate. GPU memory is unavailable because CUDA/NVML access is blocked.

## Conclusion

No conclusion about DINOv3 accuracy or cost is possible until the approved official assets are supplied. Among completed DINOv2 conditions, budget R2 is the best accuracy/cost tradeoff observed (7/10 full, 5/7 marker subset), but NT remains unresolved and these repeatedly used development images cannot establish deployment reliability.

## Reproduction

```bash
# DINOv2
/home/mohamed/miniconda3/envs/ml/bin/python evaluate_plate_representations.py --manifest controlled_plate_manifest_historical_remap.json --input-mode budget --output-dir <fresh-dir>

# Official DINOv3-S (repeat with dinov3_vitb16 and its exact B checkpoint)
/home/mohamed/miniconda3/envs/ml/bin/python evaluate_plate_representations.py --manifest controlled_plate_manifest_historical_remap.json --encoder dinov3_vits16 --dinov3-repo /path/to/official/dinov3 --dinov3-checkpoint /path/to/dinov3_vits16_pretrain_lvd1689m-08c60483.pth --input-mode budget --output-dir <fresh-dir>
```
