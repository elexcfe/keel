import assert from 'node:assert/strict';
import {mkdtempSync, readFileSync, rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join, resolve} from 'node:path';
import {pathToFileURL} from 'node:url';
import commandTimeout from '../src/command_timeout.mjs';

let handler;
commandTimeout({on: (event, fn) => { assert.equal(event, 'tool_call'); handler = fn; }});
for (const [requested, expected] of [[undefined,120],[0,120],[-1,120],[Infinity,120],[300,300],[99999,600],[0.2,0.2]]) {
  const event = {toolName:'bash',input:{command:'sleep 30',timeout:requested}};
  handler(event); assert.equal(event.input.timeout,expected);
}
const other = {toolName:'read',input:{path:'a'}};
handler(other); assert.equal(other.input.timeout,undefined);

// 指向本机已安装 Pi；不用模型、不联网。未提供时只运行策略测试。
if (process.env.PI_PACKAGE_PATH) {
  const base=resolve(process.env.PI_PACKAGE_PATH);
  const moduleAt=(p)=>import(pathToFileURL(join(base,p)).href);
  const {loadExtensions}=await moduleAt('dist/core/extensions/loader.js');
  const {ExtensionRunner}=await moduleAt('dist/core/extensions/runner.js');
  const {createBashTool}=await moduleAt('dist/core/tools/bash.js');
  const root=mkdtempSync(join(tmpdir(),'keel-pi-timeout-'));
  try {
    const loaded=await loadExtensions([resolve('script_tool/make_bug/src/command_timeout.mjs')],root);
    assert.deepEqual(loaded.errors,[]); assert.equal(loaded.extensions.length,1);
    const runner=new ExtensionRunner(loaded.extensions,loaded.runtime,root,{},{});
    const tool=createBashTool(root);
    const event={type:'tool_call',toolName:'bash',toolCallId:'timeout-test',input:{command:'sleep 30 & echo $! > child.pid; wait',timeout:0.2}};
    await runner.emitToolCall(event);
    const start=Date.now();
    await assert.rejects(tool.execute(event.toolCallId,event.input),/timed out|timeout/i);
    assert.ok(Date.now()-start<3000,'超时应及时返回');
    const pid=Number(readFileSync(join(root,'child.pid'),'utf8'));
    let alive=false;
    try {const stat=readFileSync(`/proc/${pid}/stat`,'utf8');alive=!stat.slice(stat.lastIndexOf(')')+2).startsWith('Z');} catch {}
    assert.equal(alive,false,'超时后子进程不应继续执行');
    const next={type:'tool_call',toolName:'bash',toolCallId:'after-timeout',input:{command:'printf recovered'}};
    await runner.emitToolCall(next);assert.equal(next.input.timeout,120);
    const result=await tool.execute(next.toolCallId,next.input);
    assert.match(JSON.stringify(result),/recovered/);
    console.log('PASS: actual Pi extension loading, timeout, child cleanup, subsequent command');
  } finally {rmSync(root,{recursive:true,force:true});}
}
console.log('PASS: default timeout, explicit timeout, upper cap, invalid input, unrelated tools');
