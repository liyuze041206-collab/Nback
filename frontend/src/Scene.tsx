import {Component,Suspense,useEffect,useRef,useState,type ReactNode} from 'react';
import {Canvas,useFrame,useThree} from '@react-three/fiber';
import {Edges,Html,Line,OrbitControls,PerspectiveCamera} from '@react-three/drei';
import {Group,MathUtils,Vector3} from 'three';
import type {OrbitControls as OrbitControlsImpl} from 'three-stdlib';
import {modules,inferenceKeys,trainKeys,innerKeys,type ModuleKey} from './data';

type Props={explode:number,mode:'inference'|'train',inside:boolean,selected:ModuleKey,onSelect:(key:ModuleKey)=>void,playing:boolean,reset:number,reduced:boolean};
class Boundary extends Component<{children:ReactNode,fallback:ReactNode},{failed:boolean}>{state={failed:false};static getDerivedStateFromError(){return {failed:true}}render(){return this.state.failed?this.props.fallback:this.props.children}}

function Board({id,target,selected,onSelect,expanded,reduced}:{id:ModuleKey,target:[number,number,number],selected:boolean,onSelect:()=>void,expanded:boolean,reduced:boolean}){
 const group=useRef<Group>(null!);const m=modules[id];const [hover,setHover]=useState(false);
 useFrame((_,dt)=>{if(group.current){if(reduced)group.current.position.set(...target);else group.current.position.lerp(new Vector3(...target),1-Math.exp(-dt*6))}});
 return <group ref={group} position={target}>
  <mesh onClick={e=>{e.stopPropagation();onSelect()}} onPointerOver={()=>{setHover(true);document.body.style.cursor='pointer'}} onPointerOut={()=>{setHover(false);document.body.style.cursor='auto'}}>
   <boxGeometry args={[1.38,1.8,.22]}/><meshStandardMaterial color={m.color} transparent opacity={selected?.46:hover?.35:.16} roughness={.3} metalness={.25}/><Edges color={selected?'#dcfff2':m.color} linewidth={selected?1.5:1}/>
  </mesh>
  {Array.from({length:35},(_,i)=><mesh key={i} position={[(i%5-2)*.23,(Math.floor(i/5)-3)*.22,.13]}><sphereGeometry args={[id==='prototype'?.034:.018,6,6]}/><meshBasicMaterial color={m.color} transparent opacity={selected?.9:.5}/></mesh>)}
  {(expanded||selected)&&<Html position={[0,-1.23,0]} center style={{pointerEvents:'none'}}><div className={`scene-label ${selected?'selected':''}`}><span>{m.en}</span><strong>{m.title}</strong></div></Html>}
 </group>
}
function World(p:Props){
 const {size}=useThree();const distance=size.width/size.height<1.5?19:12;
 const controls=useRef<OrbitControlsImpl>(null!);const orb=useRef<Group>(null!);
 const keys=p.inside?innerKeys:p.mode==='train'?trainKeys:inferenceKeys;
 const positions=keys.map((_,i):[number,number,number]=>{
  if(p.inside){const grid:[number,number,number][]=[[-3,1.5,0],[-1.3,1.5,0],[.4,1.5,0],[2.1,1.5,0],[.4,-1.3,0],[-2.3,-1.3,0],[3.2,-1.3,0]];return grid[i].map((v,k)=>k===2?v:v*(.35+.65*p.explode)) as [number,number,number]}
  if(p.mode==='train'&&i>=3)return [2.6+(p.explode*1.1),(i===3?1.55:-1.55)*p.explode,(i-2)*.3];
  return [(i-2)*(.38+2.15*p.explode),Math.sin(i*.9)*.26,(i-2)*(.38-.22*p.explode)]
 });
 const edges=p.inside?[[0,4],[1,4],[2,4],[3,4],[4,6],[5,6]]:p.mode==='train'?[[0,1],[1,2],[2,3],[2,4]]:[[0,1],[1,2],[2,3],[3,4]];
 useEffect(()=>{controls.current?.reset()},[p.reset]);
 useFrame(({clock})=>{if(!orb.current)return;orb.current.visible=p.playing&&!p.reduced;if(orb.current.visible){const travel=(clock.elapsedTime*.42)%edges.length;const [a,b]=edges[Math.floor(travel)];orb.current.position.copy(new Vector3(...positions[a]).lerp(new Vector3(...positions[b]),travel%1))}});
 return <><PerspectiveCamera makeDefault position={p.inside?[2,3.5,distance+2]:[2,3.2,distance]} fov={38}/><ambientLight intensity={1.5}/><directionalLight position={[2,6,8]} intensity={3}/><pointLight position={[-5,1,3]} intensity={20} color="#76e4c4"/>
 <group rotation={[0,-.15,0]} scale={p.inside?.85:1}>
 {edges.map(([a,b],i)=><Line key={i} points={[positions[a],positions[b]]} color="#749ba0" transparent opacity={.4} lineWidth={1} dashed dashSize={.1} gapSize={.1}/>)}
 {keys.map((id,i)=><Board key={id} id={id} target={positions[i]} selected={id===p.selected} onSelect={()=>p.onSelect(id)} expanded={p.explode>.65} reduced={p.reduced}/>)}
 <group ref={orb}><mesh><sphereGeometry args={[.055,12,12]}/><meshBasicMaterial color="#c1fff0"/></mesh></group>
 </group>
 <gridHelper args={[22,38,'#26383c','#152127']} position={[0,-2.7,0]}/><OrbitControls ref={controls} enablePan={false} minDistance={7} maxDistance={26} maxPolarAngle={Math.PI*.8} minPolarAngle={Math.PI*.13} enableDamping={!p.reduced} dampingFactor={.08}/></>
}
export function FlatNetwork(p:Props){const keys=p.inside?innerKeys:p.mode==='train'?trainKeys:inferenceKeys;return <div className="flat-network" aria-label="二维网络结构"><p>二维结构视图 · 按顺序选择模块</p><div>{keys.map((id,i)=><button key={id} className={p.selected===id?'active':''} onClick={()=>p.onSelect(id)}><span>0{i+1}</span><strong>{modules[id].title}</strong><small>{modules[id].output}</small></button>)}</div></div>}
export default function NetworkScene(p:Props){const [supported,setSupported]=useState<boolean|null>(null);useEffect(()=>{try{const canvas=document.createElement('canvas');const gl=canvas.getContext('webgl2');setSupported(!!gl);gl?.getExtension('WEBGL_lose_context')?.loseContext()}catch{setSupported(false)}return()=>{document.body.style.cursor='auto'}},[]);if(supported===null)return <div className="scene-loading">正在准备网络视图…</div>;if(!supported)return <FlatNetwork {...p}/>;return <Boundary fallback={<FlatNetwork {...p}/>}><Suspense fallback={<div className="scene-loading">正在准备网络视图…</div>}><Canvas dpr={[1,1.6]} gl={{antialias:true,alpha:true}} style={{width:'100%',height:'100%'}}><World {...p}/></Canvas></Suspense></Boundary>}
