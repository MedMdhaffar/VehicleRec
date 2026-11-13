"""T1 console API. Only fixed errors and allowlisted public projections escape."""
from django.http import FileResponse
from django.views.decorators.debug import sensitive_post_parameters, sensitive_variables
from rest_framework.decorators import api_view, parser_classes
from rest_framework.parsers import JSONParser, MultiPartParser, FormParser
from rest_framework.response import Response
from common.console_runs import RunStore, ActiveRunError, ServerProcessError
from common.console_stack import readiness, require_delivery, StackUnavailable


@api_view(['GET'])
def stack_status(request):
    result = readiness()
    response = Response(result)
    response['Cache-Control'] = 'no-store'
    return response


def server_process_error():
    return Response({'error':'Vehicle Console requires a single backend process for this run root.'}, status=503)


@sensitive_post_parameters()
@api_view(['GET','POST'])
@parser_classes([JSONParser, MultiPartParser, FormParser])
@sensitive_variables()
def runs(request):
    try:
        store = RunStore()
        if request.method == 'GET':
            response = Response({'runs':store.list(active_only=request.query_params.get('active')=='1')})
            response['Cache-Control'] = 'no-store'
            return response
        data = request.data
        if 'max_frames' in data:
            raise ValueError('obsolete_frame_limit')
        require_delivery()
        enabled = data.get('manufacturer_enabled', False)
        if enabled in ('true','false'):enabled = enabled == 'true'
        result = store.start(source_kind=data.get('source_kind'), upload=request.FILES.get('video'),
            rtsp_uri=data.get('rtsp_uri'), manufacturer=enabled,
            rtsp_transport=data.get('rtsp_transport', 'tcp'),
            sample_every=data.get('sample_every'), max_seconds=data.get('max_seconds'))
        response = Response(result,status=202)
    except ServerProcessError:
        response = server_process_error()
    except ActiveRunError:
        response = Response({'error':'Stop the current run before starting another.'},status=409)
    except StackUnavailable as error:
        response = Response({'error':str(error)}, status=503)
    except Exception:
        response = Response({'error':'Could not start. Check the input and run limits.'},status=400)
    response['Cache-Control'] = 'no-store'
    return response


@api_view(['GET'])
def run_detail(request, run_id):
    try:response = Response(RunStore().status(str(run_id)))
    except ServerProcessError:response = server_process_error()
    except Exception:response = Response({'error':'Run not found.'},status=404)
    response['Cache-Control'] = 'no-store'
    return response


@api_view(['POST'])
def run_stop(request, run_id):
    try:response = Response(RunStore().stop(str(run_id)))
    except ServerProcessError:response = server_process_error()
    except Exception:response = Response({'error':'Run not found.'},status=404)
    response['Cache-Control'] = 'no-store'
    return response


@api_view(['GET'])
def run_preview(request, run_id):
    try:
        directory = RunStore().directory(str(run_id))
        path = directory/'live-preview.jpg'
        if not path.exists():path=directory/'preview.jpg'
        response = FileResponse(path.open('rb'),content_type='image/jpeg')
    except ServerProcessError:response = server_process_error()
    except Exception:response = Response({'error':'Preview is not available yet.'},status=404)
    response['Cache-Control'] = 'no-store'
    response['X-Content-Type-Options'] = 'nosniff'
    return response


@api_view(['GET'])
def run_crop(request, run_id, track_id, kind):
    try:
        store = RunStore()
        directory = store.directory(str(run_id))
        if directory.is_symlink() or not any(row['track_id'] == track_id for row in store.status(str(run_id))['rows']):
            raise FileNotFoundError()
        from common.console_crops import thumbnail_path
        response = FileResponse(thumbnail_path(directory, track_id, kind).open('rb'), content_type='image/png')
    except ServerProcessError:response = server_process_error()
    except Exception:response = Response({'error':'Crop is unavailable for this track and run.'}, status=404)
    response['Cache-Control'] = 'no-store'
    response['X-Content-Type-Options'] = 'nosniff'
    return response
