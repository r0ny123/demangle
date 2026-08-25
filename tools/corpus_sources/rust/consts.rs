// Const generic arguments of every shape v0 can encode.
//
// Integers and `bool`/`char` come out of ordinary stable code, but the structural
// encodings -- arrays `KA`, tuples `KT`, struct and enum-variant values `KV`, and
// references to string data `KR` -- need `adt_const_params`, so this file is nightly-only
// and is compiled through the same `RUSTC_BOOTSTRAP` escape hatch the legacy mangling
// scheme already needs. These are the shapes a hand-written test file never contains and
// that a v0 parser is most likely to have skipped.
#![allow(dead_code, incomplete_features, clippy::all)]
#![feature(adt_const_params, unsized_const_params)]
use std::marker::ConstParamTy;

#[derive(PartialEq, Eq, ConstParamTy, Debug)]
pub struct Point { pub x: i32, pub y: u8 }

#[derive(PartialEq, Eq, ConstParamTy, Debug)]
pub enum Kind { A, B(u8), C { z: i16 } }

#[inline(never)]
pub fn with_struct<const P: Point>() -> i32 { P.x }
#[inline(never)]
pub fn with_enum<const K: Kind>() -> u8 { match K { Kind::A => 0, Kind::B(v) => v, Kind::C { z } => z as u8 } }
#[inline(never)]
pub fn with_str<const S: &'static str>() -> usize { S.len() }
#[inline(never)]
pub fn with_arr<const A: [u8; 3]>() -> u8 { A[0] }
#[inline(never)]
pub fn with_tuple<const T: (u8, bool)>() -> u8 { T.0 }

fn main() {
    let mut t = 0usize;
    t += with_struct::<{ Point { x: -5, y: 200 } }>() as usize;
    t += with_enum::<{ Kind::A }>() as usize;
    t += with_enum::<{ Kind::B(3) }>() as usize;
    t += with_enum::<{ Kind::C { z: -2 } }>() as usize;
    t += with_str::<"héllo">();
    t += with_str::<"">();
    t += with_arr::<{ [1, 2, 3] }>() as usize;
    t += with_tuple::<{ (7, true) }>() as usize;
    println!("{t}");
}
