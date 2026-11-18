export type Json = Record<string, any>
export class ApiError extends Error {
  constructor(public status:number,message:string){super(message)}
}
export function client(token:()=>string){
  const headers=():Record<string,string>=>token()?{'Authorization':'Bearer '+token()}:{}
  async function request<T=Json>(path:string,method='GET',body?:unknown):Promise<T>{
    const r=await fetch('/api/v1'+path,{method,headers:new Headers({'Content-Type':'application/json',...headers()}),body:body===undefined?undefined:JSON.stringify(body)})
    const result=await r.json()
    if(!r.ok)throw new ApiError(r.status,typeof result.detail==='string'?result.detail:JSON.stringify(result.detail))
    return result as T
  }
  async function supportStream(path:string, body:unknown, onNode:(node:string)=>void){
    const response=await fetch('/api/v1'+path,{
      method:'POST',
      headers:{'Content-Type':'application/json',Accept:'text/event-stream',...headers()},
      body:JSON.stringify(body),
    })
    if(!response.ok){
      const result=await response.json()
      throw new ApiError(response.status,typeof result.detail==='string'?result.detail:JSON.stringify(result.detail))
    }
    if(!response.body)throw new Error('客服事件流不可用')
    const reader=response.body.pipeThrough(new TextDecoderStream()).getReader()
    let buffer=''
    try {
      for(;;){
        const {done,value}=await reader.read()
        if(done)throw new Error('客服连接中断，请先刷新会话查看结果')
        buffer+=value
        let boundary:number
        while((boundary=buffer.indexOf('\n\n'))>=0){
          const frame=buffer.slice(0,boundary)
          buffer=buffer.slice(boundary+2)
          const [eventLine,dataLine]=frame.split('\n')
          if(!eventLine?.startsWith('event: ')||!dataLine?.startsWith('data: '))continue
          const event=eventLine.slice(7)
          const payload=JSON.parse(dataLine.slice(6))
          if(event==='node')onNode(payload.node)
          if(event==='error')throw new ApiError(payload.status,payload.detail)
          if(event==='complete')return
        }
      }
    } finally {
      await reader.cancel()
      reader.releaseLock()
    }
  }
  async function download(path:string,name:string){
    const r=await fetch('/api/v1'+path,{headers:new Headers(headers())})
    if(!r.ok)throw new ApiError(r.status,'下载失败')
    const url=URL.createObjectURL(await r.blob());const a=document.createElement('a');a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000)
  }
  return {request,download,supportStream}
}
