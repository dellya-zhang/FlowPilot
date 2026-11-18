export type Json = Record<string, any>
export class ApiError extends Error {
  constructor(public status:number,message:string){super(message)}
}
export function client(token:()=>string){
  const headers=()=>token()?{'Authorization':'Bearer '+token()}:{}
  async function request<T=Json>(path:string,method='GET',body?:unknown):Promise<T>{
    const r=await fetch('/api/v1'+path,{method,headers:new Headers({'Content-Type':'application/json',...headers()}),body:body===undefined?undefined:JSON.stringify(body)})
    const result=await r.json()
    if(!r.ok)throw new ApiError(r.status,typeof result.detail==='string'?result.detail:JSON.stringify(result.detail))
    return result as T
  }
  async function download(path:string,name:string){
    const r=await fetch('/api/v1'+path,{headers:new Headers(headers())})
    if(!r.ok)throw new ApiError(r.status,'下载失败')
    const url=URL.createObjectURL(await r.blob());const a=document.createElement('a');a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000)
  }
  return {request,download}
}
