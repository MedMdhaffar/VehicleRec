"""Pure, explicit local artifact identities. No inference imports or SQL keys."""
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Callable
from contracts.db1.reference import canonical_json_bytes
from .db1_rows.core import require,integer,frozen_tree
from .plate_metadata import PlateModel,_name


def file_hash(path):
    digest=sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda:stream.read(1024*1024),b''):digest.update(chunk)
    return digest.hexdigest()


@dataclass(frozen=True,slots=True)
class ArtifactFile:
    label: str
    path: str
    size: int
    sha256: str

    @classmethod
    def capture(cls,label,path):
        path=Path(path).resolve(strict=True)
        return cls(label,str(path),path.stat().st_size,file_hash(path))


@dataclass(frozen=True,slots=True)
class LocalArtifact:
    upstream_name: str
    task: str
    files: tuple[ArtifactFile,...]
    versions: tuple[tuple[str,str],...]
    parameters: tuple[tuple[str,str],...]
    device: str = 'cpu'

    def __post_init__(self):
        frozen_tree(self)
        _name(self.upstream_name)
        require(self.task in {'ocr','detect'} and self.device=='cpu','INVALID_ARTIFACT','CPU synchronous profile only')
        require(type(self.files) is tuple and 1<=len(self.files)<=8 and all(type(f) is ArtifactFile for f in self.files)
                and len({f.label for f in self.files})==len(self.files),'INVALID_ARTIFACT','explicit unique artifact files required')
        for f in self.files:
            _name(f.label);require(Path(f.path).is_absolute(),'INVALID_ARTIFACT','absolute local artifact path required')
            integer(f.size,512*1024*1024,'artifact bytes')
            require(type(f.sha256) is str and len(f.sha256)==64 and all(c in '0123456789abcdef' for c in f.sha256),
                    'INVALID_ARTIFACT','SHA256 artifact identity required')
        for values in (self.versions,self.parameters):
            require(type(values) is tuple and len(values)<=16 and len({k for k,v in values})==len(values),
                    'INVALID_ARTIFACT','bounded immutable unique runtime/configuration bindings required')
            for k,v in values:_name(k);_name(v)
        require(bool(self.versions),'INVALID_ARTIFACT','observed runtime versions required')

    def material(self):
        return dict(profile='engine-local-artifact-v1',upstream_name=self.upstream_name,task=self.task,device=self.device,
            files={f.label:dict(bytes=f.size,sha256=f.sha256) for f in self.files},
            versions=dict(self.versions),parameters=dict(self.parameters))

    @property
    def digest(self):return sha256(canonical_json_bytes(self.material())).hexdigest()
    @property
    def model(self):
        # Integer 1 identifies this content-addressed binding format, not a
        # fabricated training release/DB registration. The digest carries version.
        return PlateModel('local-'+self.task+'-sha256:'+self.digest,1,self.task)

    def verify(self):
        for f in self.files:
            path=Path(f.path)
            require(path.is_file() and path.stat().st_size==f.size and file_hash(path)==f.sha256,
                    'ARTIFACT_BINDING_CONFLICT','local artifact differs from immutable binding')


@dataclass(frozen=True,slots=True)
class LocalExecutor:
    executor_id: str
    artifact: LocalArtifact
    kind: str
    invoke: Callable
    decode: Callable

    def __post_init__(self):
        _name(self.executor_id)
        require(type(self.artifact) is LocalArtifact and callable(self.invoke) and callable(self.decode),
                'INVALID_EXECUTOR','explicit artifact-bound native invocation/decoder required')
        require(self.kind in {'whole_ocr','marker','left_ocr','right_ocr'}
                and (self.artifact.task=='detect')==(self.kind=='marker'),
                'INVALID_EXECUTOR','explicit native operation kind/task binding required')

    @property
    def model(self):return self.artifact.model
