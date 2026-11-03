"""Account-scoped browser workspace storage; never adopt unidentified legacy data."""
import hashlib
import json


STORAGE_SCRIPT = r"""
  function workspaceStorage(kind, prefix){
    return {
      getItem:function(key){return prefix ? window[kind].getItem(prefix+key) : null;},
      setItem:function(key,value){if(prefix)window[kind].setItem(prefix+key,value);},
      removeItem:function(key){if(prefix)window[kind].removeItem(prefix+key);}
    };
  }
  const workspaceStoragePrefix=__STORAGE_PREFIX__;
  const localStorage=workspaceStorage('localStorage',workspaceStoragePrefix);
  const sessionStorage=workspaceStorage('sessionStorage',workspaceStoragePrefix);
"""


def storage_prefix(account_id):
    account_id = str(account_id or "").strip()
    if not account_id:
        return ""
    return "tp-workspace-v1:" + hashlib.sha256(account_id.encode()).hexdigest() + ":"


def storage_script(account_id):
    return STORAGE_SCRIPT.replace("__STORAGE_PREFIX__", json.dumps(storage_prefix(account_id)))
