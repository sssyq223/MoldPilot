<script setup lang="ts">
import {computed} from 'vue'

type InlineToken={type:'text'|'strong'|'em'|'code'|'link'|'break';text?:string;href?:string}
type MarkdownBlock={type:'paragraph'|'heading'|'unordered'|'ordered'|'quote'|'code';level?:number;content?:InlineToken[];items?:InlineToken[][];text?:string}

const props=withDefaults(defineProps<{text?:string;inline?:boolean}>(),{text:'',inline:false})

function safeHref(value:string){
 try{
  const url=new URL(value,window.location.origin)
  return ['http:','https:'].includes(url.protocol)?url.href:''
 }catch{return ''}
}

function inlineTokens(value:string):InlineToken[]{
 const tokens:InlineToken[]=[]
 const pattern=/(\*\*[^*\n]+\*\*|__[^_\n]+__|`[^`\n]+`|\[[^\]\n]+\]\((?:https?:\/\/)[^)\s]+\)|\n)/g
 let cursor=0
 for(const match of value.matchAll(pattern)){
  const start=match.index??0,raw=match[0]
  if(start>cursor)tokens.push({type:'text',text:value.slice(cursor,start)})
  if(raw==='\n')tokens.push({type:'break'})
  else if(raw.startsWith('**')||raw.startsWith('__'))tokens.push({type:'strong',text:raw.slice(2,-2)})
  else if(raw.startsWith('`'))tokens.push({type:'code',text:raw.slice(1,-1)})
  else if(raw.startsWith('[')){
   const parts=raw.match(/^\[([^\]]+)\]\((.+)\)$/)
   const href=parts?safeHref(parts[2]):''
   tokens.push(href?{type:'link',text:parts?.[1],href}:{type:'text',text:raw})
  }else tokens.push({type:'text',text:raw})
  cursor=start+raw.length
 }
 if(cursor<value.length)tokens.push({type:'text',text:value.slice(cursor)})
 return tokens
}

function isStructural(line:string){return /^\s*(?:```|#{1,6}\s+|[-*+]\s+|\d+[.)]\s+|>\s?)/.test(line)}
function parseBlocks(value:string):MarkdownBlock[]{
 const lines=value.replace(/\r\n?/g,'\n').split('\n'),blocks:MarkdownBlock[]=[]
 for(let index=0;index<lines.length;){
  const line=lines[index]
  if(!line.trim()){index++;continue}
  if(/^\s*```/.test(line)){
   index++;const code:string[]=[]
   while(index<lines.length&&!/^\s*```/.test(lines[index]))code.push(lines[index++])
   if(index<lines.length)index++
   blocks.push({type:'code',text:code.join('\n')});continue
  }
  const heading=line.match(/^\s*(#{1,6})\s+(.+)$/)
  if(heading){blocks.push({type:'heading',level:heading[1].length,content:inlineTokens(heading[2])});index++;continue}
  const unordered=line.match(/^\s*[-*+]\s+(.+)$/)
  if(unordered){
   const items:InlineToken[][]=[]
   while(index<lines.length){const match=lines[index].match(/^\s*[-*+]\s+(.+)$/);if(!match)break;items.push(inlineTokens(match[1]));index++}
   blocks.push({type:'unordered',items});continue
  }
  const ordered=line.match(/^\s*\d+[.)]\s+(.+)$/)
  if(ordered){
   const items:InlineToken[][]=[]
   while(index<lines.length){const match=lines[index].match(/^\s*\d+[.)]\s+(.+)$/);if(!match)break;items.push(inlineTokens(match[1]));index++}
   blocks.push({type:'ordered',items});continue
  }
  if(/^\s*>\s?/.test(line)){
   const quote:string[]=[]
   while(index<lines.length&&/^\s*>\s?/.test(lines[index]))quote.push(lines[index++].replace(/^\s*>\s?/,''))
   blocks.push({type:'quote',content:inlineTokens(quote.join('\n'))});continue
  }
  const paragraph=[line.trim()];index++
  while(index<lines.length&&lines[index].trim()&&!isStructural(lines[index]))paragraph.push(lines[index++].trim())
  blocks.push({type:'paragraph',content:inlineTokens(paragraph.join('\n'))})
 }
 return blocks
}

const inlineContent=computed(()=>inlineTokens(props.text||''))
const blocks=computed(()=>parseBlocks(props.text||''))
</script>

<template>
<span v-if="inline" class="markdown-inline"><template v-for="(token,index) in inlineContent" :key="index"><strong v-if="token.type==='strong'">{{token.text}}</strong><em v-else-if="token.type==='em'">{{token.text}}</em><code v-else-if="token.type==='code'">{{token.text}}</code><a v-else-if="token.type==='link'" :href="token.href" target="_blank" rel="noopener noreferrer">{{token.text}}</a><br v-else-if="token.type==='break'"/><template v-else>{{token.text}}</template></template></span>
<div v-else class="markdown-body"><template v-for="(block,blockIndex) in blocks" :key="blockIndex"><component :is="`h${block.level}`" v-if="block.type==='heading'" class="markdown-heading"><template v-for="(token,index) in block.content" :key="index"><strong v-if="token.type==='strong'">{{token.text}}</strong><code v-else-if="token.type==='code'">{{token.text}}</code><a v-else-if="token.type==='link'" :href="token.href" target="_blank" rel="noopener noreferrer">{{token.text}}</a><br v-else-if="token.type==='break'"/><template v-else>{{token.text}}</template></template></component><ul v-else-if="block.type==='unordered'"><li v-for="(item,itemIndex) in block.items" :key="itemIndex"><template v-for="(token,index) in item" :key="index"><strong v-if="token.type==='strong'">{{token.text}}</strong><code v-else-if="token.type==='code'">{{token.text}}</code><a v-else-if="token.type==='link'" :href="token.href" target="_blank" rel="noopener noreferrer">{{token.text}}</a><template v-else>{{token.text}}</template></template></li></ul><ol v-else-if="block.type==='ordered'"><li v-for="(item,itemIndex) in block.items" :key="itemIndex"><template v-for="(token,index) in item" :key="index"><strong v-if="token.type==='strong'">{{token.text}}</strong><code v-else-if="token.type==='code'">{{token.text}}</code><a v-else-if="token.type==='link'" :href="token.href" target="_blank" rel="noopener noreferrer">{{token.text}}</a><template v-else>{{token.text}}</template></template></li></ol><blockquote v-else-if="block.type==='quote'"><template v-for="(token,index) in block.content" :key="index"><strong v-if="token.type==='strong'">{{token.text}}</strong><code v-else-if="token.type==='code'">{{token.text}}</code><br v-else-if="token.type==='break'"/><template v-else>{{token.text}}</template></template></blockquote><pre v-else-if="block.type==='code'"><code>{{block.text}}</code></pre><p v-else><template v-for="(token,index) in block.content" :key="index"><strong v-if="token.type==='strong'">{{token.text}}</strong><code v-else-if="token.type==='code'">{{token.text}}</code><a v-else-if="token.type==='link'" :href="token.href" target="_blank" rel="noopener noreferrer">{{token.text}}</a><br v-else-if="token.type==='break'"/><template v-else>{{token.text}}</template></template></p></template></div>
</template>
