"""Fresh bounded namespace claim and same-live-session reconnect checks."""
from contracts.db1.reference import canonical_json_bytes
from ..db1_rows.core import require

CLAIM_ENGINE = '''
local keys=redis.call('KEYS',ARGV[1]..'*')
if #keys>64 then return {-1,'CAPACITY'} end
for _,key in ipairs(keys) do
 local kind=redis.call('TYPE',key).ok
 if key==KEYS[1] then return {0,'OWNER_EXISTS'} end
 if kind=='stream' and redis.call('XLEN',key)>0 then return {0,'ACCEPTED_STREAM'} end
 if kind=='hash' and redis.call('HLEN',key)>0 then return {0,'ACCEPTED_STATE'} end
 if kind~='stream' and kind~='hash' and not string.match(key,':lease$') and not string.match(key,':fence$') then
  return {0,'UNKNOWN_KEY'}
 end
end
if not redis.call('SET',KEYS[1],ARGV[2],'NX') then return {0,'OWNER_EXISTS'} end
return {1}
'''


def claim_engine(client,journal,codec,context,session):
    journal.require_org(codec.org)
    require(context.org_id==codec.org,'ORG_MISMATCH','configured Engine tenant differs')
    value=canonical_json_bytes(dict(org_id=str(codec.org),run_id=context.run_id,camera_id=context.camera.camera_id,
        session=session,scope='live-engine-v2'))
    key=journal.prefix+'engine-session'
    result=client.eval(CLAIM_ENGINE,1,key,journal.prefix,value)
    require(result and result[0]==1,'NAMESPACE_NOT_FRESH','new Engine requires an unused namespace; never reset accepted work')
    return key,value
