"""Atomic read-only observation of a completed fenced Plate handoff.

PREPARE's seen/state are proposed progress. They are never completion by themselves.
All keys are in the configured org hash slot. No lease is acquired by the observer.
"""
from hashlib import sha256
from ..db1_rows.core import require
from ..redis_scalar.codec import bounded_json,vehicle_key
from .codec import digest

OBSERVE_COMPLETION = '''
-- Linearization point: PREPARE/PUBLISH/FINISH cannot interleave these reads.
if redis.call('HEXISTS',KEYS[1],'pending')==1 then return {0} end
local seen=redis.call('HGET',KEYS[1],ARGV[1])
if not seen then return {0} end
if seen~=ARGV[3] then return {-1,'INPUT_DIGEST'} end
local source=redis.call('XRANGE',KEYS[3],ARGV[4],ARGV[4],'COUNT',1)
if #source~=1 or #source[1][2]~=2 or source[1][2][1]~='json' or source[1][2][2]~=ARGV[5] then
 return {-1,'INPUT_BYTES'}
end
local pel=redis.pcall('XPENDING',KEYS[3],ARGV[6],ARGV[4],ARGV[4],1)
if pel.err then return {0} end
if #pel>0 then return {0} end
-- Empty PEL alone does not prove this physical input was delivered/ACKed.
local function number_ge(a,b)
 a=string.gsub(a,'^0+',''); b=string.gsub(b,'^0+','')
 if #a~=#b then return #a>#b end
 return a>=b
end
local function id_ge(a,b)
 local am,as=string.match(a,'^(%d+)%-(%d+)$')
 local bm,bs=string.match(b,'^(%d+)%-(%d+)$')
 if am==bm then return number_ge(as,bs) end
 return number_ge(am,bm)
end
local delivered=false
for _,group in ipairs(redis.call('XINFO','GROUPS',KEYS[3])) do
 local name=false; local cursor=false
 for i=1,#group,2 do
  if group[i]=='name' then name=group[i+1] end
  if group[i]=='last-delivered-id' then cursor=group[i+1] end
 end
 if name==ARGV[6] and cursor and id_ge(cursor,ARGV[4]) then delivered=true end
end
if not delivered then return {0} end
local state=redis.call('HGET',KEYS[1],ARGV[2])
if not state then return {0} end
local decoded=cjson.decode(state)
if decoded.status~='terminal' then return {0} end
local rows=redis.call('XRANGE',KEYS[2],'-','+','COUNT',tonumber(ARGV[8])+1)
if #rows>tonumber(ARGV[8]) then return {-1,'STREAM_CAPACITY'} end
local output=false
for _,row in ipairs(rows) do
 if #row[2]~=2 or row[2][1]~='json' then return {-1,'OUTPUT_FIELDS'} end
 local value=cjson.decode(row[2][2])
 if value.event_id==ARGV[7] then
  if output then return {-1,'OUTPUT_DUPLICATE'} end
  output=row[2][2]
 end
end
if not output then return {-1,'OUTPUT_MISSING'} end
return {1,state,output}
'''


def observe_completion(client,journal,codec,session,*,entry_id):
    journal.require_org(codec.org)
    sealed=codec.decode(session.sealed)
    expected_event=codec.v1.event_id(sealed.life,'plate.terminal',session.crop['batch_id'],0)
    result=client.eval(OBSERVE_COMPLETION,3,journal.prefix+'plate_requests:journal',journal.key('results.plate'),
        journal.key('engine.sealed.v2'),'seen:'+sealed.event_id,'vehicle:'+vehicle_key(sealed.life),
        sha256(sealed.canonical).hexdigest(),entry_id,sealed.canonical,'plate-requests-v2',expected_event,journal.max_stream)
    require(result and result[0] in {-1,0,1},'COMPLETION_MISMATCH','invalid completion observation')
    if result[0]==0:return None
    require(result[0]==1,'COMPLETION_MISMATCH','completed handoff evidence conflicts')
    state=bounded_json(result[1],max_bytes=20*1024*1024,max_depth=40,max_nodes=500000,max_string=4096)
    require(state.get('status')=='terminal' and digest(state.get('sealed'))==digest(sealed.body),
            'COMPLETION_MISMATCH','completed full lifetime/session/sealed input differs')
    terminal=codec.v1.decode(result[2])
    require(terminal.kind=='plate.terminal' and terminal.event_id==expected_event
            and digest(terminal.body)==state.get('terminal_sha256'),
            'COMPLETION_MISMATCH','completed terminal/provenance differs')
    return result[2]
