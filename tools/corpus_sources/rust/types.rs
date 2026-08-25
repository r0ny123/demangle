// Type productions, forced into symbol names on purpose.
//
// Most of the v0 type grammar only reaches a symbol name when a type appears as a
// generic argument, so nearly everything here is a call to one of the two witness
// functions below with a deliberately awkward parameter: function pointers with ABIs
// and higher-ranked binders, trait objects with associated-type bindings and lifetime
// bounds, the never type, raw pointers, arrays whose length is itself a const parameter,
// and nestings deep enough that the backreference encoding has to do real work.
#![allow(dead_code, unused_variables, improper_ctypes_definitions, clippy::all)]
#![feature(never_type)]

use std::collections::HashMap;
use std::fmt::Debug;
use std::marker::PhantomData;

/// The witness: instantiating it stamps `T` into a symbol name verbatim.
#[inline(never)]
pub fn witness<T: ?Sized>() -> usize {
    std::mem::size_of::<&T>()
}

#[inline(never)]
pub fn witness2<T: ?Sized, U: ?Sized>() -> usize {
    std::mem::size_of::<&T>() + std::mem::size_of::<&U>()
}

#[inline(never)]
pub fn witness_const<const N: usize, T>() -> usize {
    N + std::mem::size_of::<T>()
}

pub trait Marker {}
pub trait Assoc {
    type Out;
    type Other;
}
pub trait Param<T> {
    fn go(&self, t: T);
}

pub struct Concrete;
impl Marker for Concrete {}
impl Assoc for Concrete {
    type Out = u32;
    type Other = ();
}

pub struct Generic<T, U> {
    a: PhantomData<T>,
    b: PhantomData<U>,
}

pub struct Lifetimed<'a, 'b> {
    x: &'a u8,
    y: &'b u16,
}

pub struct ConstArr<const N: usize> {
    cells: [u8; N],
}

impl<const N: usize> ConstArr<N> {
    #[inline(never)]
    pub fn make() -> Self {
        ConstArr { cells: [0; N] }
    }
    #[inline(never)]
    pub fn nested<const M: usize>(&self) -> [[u8; N]; M] {
        [[0; N]; M]
    }
}

// Inherent impl on a generic self type -- `M` impl-paths carrying generic parameters.
impl<T: Debug, U: Debug> Generic<T, U> {
    #[inline(never)]
    pub fn build() -> Self {
        Generic { a: PhantomData, b: PhantomData }
    }
    #[inline(never)]
    pub fn swap(self) -> Generic<U, T> {
        Generic { a: PhantomData, b: PhantomData }
    }
}

// Trait impls whose self type is itself generic -- `X` impl-paths with arguments.
impl<T: Debug, U: Debug> Marker for Generic<T, U> {}

impl<T: Debug> Param<T> for Concrete {
    #[inline(never)]
    fn go(&self, t: T) {
        let _ = format!("{t:?}");
    }
}

impl<'a> Param<&'a str> for u8 {
    #[inline(never)]
    fn go(&self, t: &'a str) {
        let _ = t.len();
    }
}

pub fn exercise() -> usize {
    let mut total = 0usize;

    // primitives, one of each leaf code
    total += witness::<bool>();
    total += witness::<char>();
    total += witness::<()>();
    total += witness::<!>();
    total += witness::<str>();
    total += witness::<i8>() + witness::<i16>() + witness::<i32>() + witness::<i64>();
    total += witness::<i128>() + witness::<isize>();
    total += witness::<u8>() + witness::<u16>() + witness::<u32>() + witness::<u64>();
    total += witness::<u128>() + witness::<usize>();
    total += witness::<f32>() + witness::<f64>();

    // references, raw pointers, slices, arrays, tuples
    total += witness::<&'static u8>();
    total += witness::<&'static mut u8>();
    total += witness::<*const u8>();
    total += witness::<*mut *const [u8]>();
    total += witness::<[u8]>();
    total += witness::<[[i32; 4]; 2]>();
    total += witness::<(u8,)>();
    total += witness::<(u8, u16, u32, u64, i8, i16, i32, i64)>();
    total += witness::<(&'static str, [f64; 3], *mut ())>();

    // function pointers: ABIs, unsafe, higher-ranked binders, variadics-free
    total += witness::<fn()>();
    total += witness::<fn(u8) -> u8>();
    total += witness::<fn(u8, u16, u32)>();
    total += witness::<unsafe fn(u8) -> u8>();
    total += witness::<extern "C" fn(u8) -> u8>();
    total += witness::<unsafe extern "C" fn(*const u8, usize) -> i32>();
    total += witness::<extern "system" fn(u16)>();
    total += witness::<for<'a> fn(&'a u8) -> &'a u8>();
    total += witness::<for<'a, 'b> fn(&'a u8, &'b u16) -> &'a u8>();
    total += witness::<fn(fn(fn(u8)))>();

    // trait objects: plain, with markers, with lifetimes, with bindings
    total += witness::<dyn Marker>();
    total += witness::<dyn Marker + Send + Sync>();
    total += witness::<dyn Marker + 'static>();
    total += witness::<dyn Assoc<Out = u32, Other = ()>>();
    total += witness::<dyn Param<u8>>();
    total += witness::<dyn for<'a> Param<&'a str>>();
    total += witness::<dyn Fn(u8) -> u16>();
    total += witness::<dyn FnMut(&str) -> String + Send>();
    total += witness::<dyn Iterator<Item = Vec<u8>>>();
    total += witness::<Box<dyn Iterator<Item = Box<dyn Marker + Send>> + Send + Sync + 'static>>();

    // deep nesting, to make the backreference encoder work
    total += witness::<HashMap<String, Vec<Option<Box<[Result<u8, String>]>>>>>();
    total += witness::<Generic<Generic<u8, u16>, Generic<u16, u8>>>();
    total += witness::<Generic<Generic<Generic<u8, u8>, u8>, Generic<u8, Generic<u8, u8>>>>();
    total += witness2::<u8, u8>();
    total += witness2::<Vec<u8>, Vec<u8>>();
    total += witness2::<dyn Marker, dyn Marker>();
    total += witness2::<fn(u8) -> u8, fn(u8) -> u8>();

    // lifetimes in struct positions
    total += witness::<Lifetimed<'static, 'static>>();
    total += witness::<&'static Lifetimed<'static, 'static>>();

    // const generics of every flavour
    total += witness_const::<0, u8>();
    total += witness_const::<1, u8>();
    total += witness_const::<255, u8>();
    total += witness_const::<4096, u16>();
    total += witness_const::<18446744073709551615, u32>();
    total += witness::<ConstArr<0>>();
    total += witness::<ConstArr<7>>();
    total += ConstArr::<3>::make().cells.len();
    total += ConstArr::<3>::make().nested::<5>().len();
    total += witness::<[u8; 0]>() + witness::<[u8; 1024]>();

    total += Generic::<u8, i16>::build().swap().a.is_send_hack();
    total += Generic::<Vec<u8>, String>::build().swap().a.is_send_hack();

    Concrete.go(1u8);
    Concrete.go("s");
    Concrete.go(vec![1u16]);
    1u8.go("t");

    total
}

trait SendHack {
    fn is_send_hack(&self) -> usize;
}
impl<T> SendHack for PhantomData<T> {
    #[inline(never)]
    fn is_send_hack(&self) -> usize {
        0
    }
}

fn main() {
    println!("{}", exercise());
}
