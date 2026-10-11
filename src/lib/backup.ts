import {desktop} from './desktop'
export const backup = <T,>(op:string,args:Record<string,unknown>={}) => {
  if(!desktop) return Promise.reject(new Error('Backup and device access require the standalone Windows app. Your browser media library is still available.'))
  return desktop.request<T>(`backup:${op}`,args)
}
export type BackupDevice={id:string;label:string;kind:string;root:string;total_bytes:number|null;free_bytes:number|null}
export type BackupFile={source_path:string;original_source_path?:string;source_name?:string;name?:string;size:number;category:string;parent_folder?:string;relative_path?:string;status?:string;sha256?:string;proof_path?:string;target_path?:string;duplicate_of?:string;error?:string;restore_path?:string;cleanup_status?:string}
export type Inventory={id:string;count:number;bytes:number;categories:[string,number,number][];folders:[string,number,number][];majorFolders:[string,number,number][];files:BackupFile[];filteredCount:number}
export type BackupJob={id:string;status:string;total_files:number;total_bytes:number;completed_files?:number;completed_bytes?:number;copied_files?:number;skipped_files?:number;failed_files?:number;restored_files?:number;conflict_files?:number;deleted_files?:number;freed_bytes?:number;target_root?:string;source_ref?:string;source_kind?:string;queue_type?:string;updated_at:string}
export type BackupTask={id:string;kind:string;state:string;done:number;bytes:number;current:string;error?:string;result?:Record<string,unknown>}
export type BackupOverview={statePath:string;active:(BackupTask&{total?:number})|null;jobs:BackupJob[];restores:BackupJob[];cleanups:BackupJob[];tasks:BackupTask[];totals:{backup:number;restore:number;cleanup:number;copied:number;skipped:number};categories:Record<string,string>;destinations:Record<string,string>;volumes:{id:string;label:string;root:string;kind:string}[];health:{volume_id:string;label:string;root:string;recommendation:string;indexed_at?:string}[]}
export type BackupSearchResult={name:string;category:string;size:number;location:string;original_source:string;source_type:string;modified_at:string;score:number}
