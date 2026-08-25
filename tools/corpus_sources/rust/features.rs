// A spread of Rust constructs chosen for the *symbol names* they produce rather than
// for anything they compute. Anything whose mangling has a distinct shape belongs here:
// generics, trait impls, closures, associated types, lifetimes, const generics,
// trait objects, `impl Trait`, and the standard-library containers whose monomorphised
// instantiations dominate a real binary's symbol table.
#![allow(dead_code, unused_variables, unused_mut, clippy::all)]

use std::collections::{BTreeMap, BTreeSet, HashMap, HashSet, VecDeque};
use std::fmt::{self, Debug, Display};
use std::rc::Rc;
use std::sync::{Arc, Mutex};

pub mod outer {
    pub mod inner {
        pub mod deeper {
            #[inline(never)]
            pub fn buried(x: u32) -> u32 {
                x.wrapping_mul(3)
            }
        }

        #[inline(never)]
        pub fn plain(x: i32) -> i32 {
            x + 1
        }

        #[inline(never)]
        pub fn with_statics() -> &'static [u8] {
            static LOCAL: [u8; 4] = [1, 2, 3, 4];
            &LOCAL
        }

        pub struct Base {
            pub value: i32,
        }

        impl Base {
            #[inline(never)]
            pub fn new(value: i32) -> Self {
                Base { value }
            }
            #[inline(never)]
            pub fn method(&self, extra: i32) -> i32 {
                self.value + extra
            }
            #[inline(never)]
            pub fn consume(self) -> i32 {
                self.value
            }
        }

        impl Drop for Base {
            fn drop(&mut self) {
                self.value = 0;
            }
        }
    }
}

// ---------------------------------------------------------------- generics

pub struct Holder<T> {
    pub value: T,
}

impl<T: Clone + Debug> Holder<T> {
    #[inline(never)]
    pub fn new(value: T) -> Self {
        Holder { value }
    }
    #[inline(never)]
    pub fn get(&self) -> T {
        self.value.clone()
    }
    #[inline(never)]
    pub fn convert<U: From<T>>(&self) -> U {
        U::from(self.value.clone())
    }
}

pub struct Pair<A, B> {
    pub first: A,
    pub second: B,
}

impl<A: Debug, B: Debug> Pair<A, B> {
    #[inline(never)]
    pub fn describe(&self) -> String {
        format!("{:?}/{:?}", self.first, self.second)
    }
}

// ------------------------------------------------------------ const generics

pub struct Matrix<const ROWS: usize, const COLS: usize> {
    pub cells: [[f64; COLS]; ROWS],
}

impl<const ROWS: usize, const COLS: usize> Matrix<ROWS, COLS> {
    #[inline(never)]
    pub fn zeroed() -> Self {
        Matrix { cells: [[0.0; COLS]; ROWS] }
    }
    #[inline(never)]
    pub fn size(&self) -> usize {
        ROWS * COLS
    }
}

#[inline(never)]
pub fn const_bool<const FLAG: bool>() -> u8 {
    if FLAG { 1 } else { 0 }
}

#[inline(never)]
pub fn const_char<const C: char>() -> u32 {
    C as u32
}

#[inline(never)]
pub fn const_signed<const N: i32>() -> i32 {
    N * 2
}

#[inline(never)]
pub fn const_wide<const N: u128>() -> u128 {
    N ^ 0xff
}

// ------------------------------------------------------------------ traits

pub trait Sided {
    const SIDES: usize;
}

pub trait Shape {
    type Unit: Debug;
    fn area(&self) -> f64;
    fn name(&self) -> &'static str {
        "shape"
    }
    fn boxed_clone(&self) -> Box<dyn Shape<Unit = Self::Unit>>;
}

#[derive(Clone, Debug)]
pub struct Circle {
    pub radius: f64,
}

#[derive(Clone, Debug)]
pub struct Square {
    pub side: f64,
}

impl Sided for Circle {
    const SIDES: usize = 0;
}

impl Sided for Square {
    const SIDES: usize = 4;
}

impl Shape for Circle {
    type Unit = f64;
    #[inline(never)]
    fn area(&self) -> f64 {
        3.14159 * self.radius * self.radius
    }
    #[inline(never)]
    fn boxed_clone(&self) -> Box<dyn Shape<Unit = f64>> {
        Box::new(self.clone())
    }
}

impl Shape for Square {
    type Unit = f64;
    #[inline(never)]
    fn area(&self) -> f64 {
        self.side * self.side
    }
    #[inline(never)]
    fn name(&self) -> &'static str {
        "square"
    }
    #[inline(never)]
    fn boxed_clone(&self) -> Box<dyn Shape<Unit = f64>> {
        Box::new(self.clone())
    }
}

impl Display for Circle {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "circle({})", self.radius)
    }
}

// generic impl of a generic trait -- exercises `<T as Trait<U>>::method` paths
pub trait Converter<T> {
    fn convert(&self, from: T) -> String;
}

impl<T: Display> Converter<T> for Circle {
    #[inline(never)]
    fn convert(&self, from: T) -> String {
        format!("{}{}", self.radius, from)
    }
}

impl<T: Display> Converter<T> for Vec<T> {
    #[inline(never)]
    fn convert(&self, from: T) -> String {
        format!("{}:{}", self.len(), from)
    }
}

// --------------------------------------------------------- lifetimes / HRTB

pub struct Borrowed<'a, T: 'a> {
    pub slot: &'a T,
}

impl<'a, T: Debug> Borrowed<'a, T> {
    #[inline(never)]
    pub fn peek(&self) -> String {
        format!("{:?}", self.slot)
    }
}

#[inline(never)]
pub fn longest<'a>(left: &'a str, right: &'a str) -> &'a str {
    if left.len() >= right.len() { left } else { right }
}

#[inline(never)]
pub fn apply_hrtb<F>(f: F) -> usize
where
    F: for<'a> Fn(&'a str) -> &'a str,
{
    f("higher ranked").len()
}

#[inline(never)]
pub fn two_lifetimes<'a, 'b>(a: &'a [u8], b: &'b [u8]) -> usize {
    a.len() + b.len()
}

// ------------------------------------------------------- dyn / impl Trait

#[inline(never)]
pub fn total_area(shapes: &[Box<dyn Shape<Unit = f64>>]) -> f64 {
    shapes.iter().map(|s| s.area()).sum()
}

#[inline(never)]
pub fn dyn_display(item: &dyn Display) -> String {
    item.to_string()
}

#[inline(never)]
pub fn dyn_multi(item: &(dyn Shape<Unit = f64> + Send + Sync)) -> f64 {
    item.area()
}

#[inline(never)]
pub fn make_adder(base: i32) -> impl Fn(i32) -> i32 {
    move |x| x + base
}

#[inline(never)]
pub fn make_iter(limit: usize) -> impl Iterator<Item = usize> {
    (0..limit).filter(|n| n % 3 == 0).map(|n| n * 2)
}

#[inline(never)]
pub fn fn_pointer_taker(f: fn(i32, i32) -> i32, g: unsafe extern "C" fn(u8) -> u8) -> i32 {
    f(1, 2)
}

// ----------------------------------------------------------------- closures

#[inline(never)]
pub fn closure_zoo(seed: i32) -> i32 {
    let plain = |x: i32| x + seed;
    let mut counter = 0;
    let mut mutating = |x: i32| {
        counter += x;
        counter
    };
    let owned = move |x: i32| x * seed;
    let nested = |x: i32| {
        let deeper = |y: i32| y - 1;
        deeper(x) + plain(x)
    };
    plain(1) + mutating(2) + owned(3) + nested(4)
}

#[inline(never)]
pub fn generic_closure<T: Copy + Debug>(items: &[T]) -> usize {
    let inspect = |t: &T| format!("{:?}", t).len();
    items.iter().map(inspect).sum()
}

// ---------------------------------------------------------------- iterators

#[inline(never)]
pub fn iterator_chain(input: &[i64]) -> Vec<String> {
    input
        .iter()
        .copied()
        .filter(|n| *n > 0)
        .map(|n| n * 2)
        .enumerate()
        .skip(1)
        .take(32)
        .zip(input.iter().rev())
        .flat_map(|((i, n), extra)| vec![format!("{i}-{n}-{extra}")])
        .collect()
}

#[inline(never)]
pub fn fold_and_scan(input: &[u32]) -> u32 {
    input
        .iter()
        .scan(0u32, |acc, n| {
            *acc = acc.wrapping_add(*n);
            Some(*acc)
        })
        .fold(0u32, |a, b| a ^ b)
}

#[inline(never)]
pub fn sort_and_dedup(mut input: Vec<String>) -> Vec<String> {
    input.sort_by(|a, b| a.len().cmp(&b.len()).then_with(|| a.cmp(b)));
    input.dedup_by(|a, b| a == b);
    input
}

// ------------------------------------------------------------ std containers

#[inline(never)]
pub fn hash_map_work(keys: &[String]) -> HashMap<String, Vec<usize>> {
    let mut map: HashMap<String, Vec<usize>> = HashMap::new();
    for (i, k) in keys.iter().enumerate() {
        map.entry(k.clone()).or_default().push(i);
    }
    map
}

#[inline(never)]
pub fn btree_work(keys: &[i32]) -> BTreeMap<i32, BTreeSet<i32>> {
    let mut map: BTreeMap<i32, BTreeSet<i32>> = BTreeMap::new();
    for k in keys {
        map.entry(*k).or_default().insert(k * 2);
    }
    map
}

#[inline(never)]
pub fn deque_work(n: usize) -> VecDeque<Box<[u8]>> {
    let mut q: VecDeque<Box<[u8]>> = VecDeque::new();
    for i in 0..n {
        q.push_back(vec![i as u8; 3].into_boxed_slice());
    }
    q
}

#[inline(never)]
pub fn shared_state(n: usize) -> Arc<Mutex<Vec<Rc<str>>>> {
    let state = Arc::new(Mutex::new(Vec::new()));
    {
        let mut guard = state.lock().unwrap();
        for i in 0..n {
            guard.push(Rc::from(format!("{i}").as_str()));
        }
    }
    state
}

#[inline(never)]
pub fn option_result(input: Option<&str>) -> Result<usize, Box<dyn std::error::Error>> {
    let value = input.ok_or("missing")?;
    let parsed: usize = value.trim().parse()?;
    Ok(parsed)
}

#[inline(never)]
pub fn hash_set_ops(a: &HashSet<u64>, b: &HashSet<u64>) -> Vec<u64> {
    a.intersection(b).copied().collect()
}

// ------------------------------------------------------ tuples / arrays / ptrs

#[inline(never)]
pub fn tuple_soup(t: (i32, (u8, f64), [u16; 4], &str)) -> usize {
    t.2.len() + t.3.len()
}

#[inline(never)]
pub fn unit_and_never(_: ()) -> ! {
    panic!("never returns")
}

#[inline(never)]
pub unsafe fn raw_pointers(p: *const u8, q: *mut [i32; 8]) -> usize {
    p as usize + q as usize
}

#[inline(never)]
pub fn slice_of_slices(s: &[&[char]]) -> usize {
    s.iter().map(|x| x.len()).sum()
}

// --------------------------------------------------------------- enums, ops

#[derive(Debug, Clone, PartialEq, Eq, PartialOrd, Ord, Hash, Default)]
pub enum Mode {
    #[default]
    Idle,
    Running(u32),
    Failed { code: i32, message: String },
}

impl std::ops::Add for Mode {
    type Output = Mode;
    #[inline(never)]
    fn add(self, other: Mode) -> Mode {
        match (self, other) {
            (Mode::Running(a), Mode::Running(b)) => Mode::Running(a + b),
            (a, _) => a,
        }
    }
}

impl std::ops::Index<usize> for Holder<Vec<u8>> {
    type Output = u8;
    #[inline(never)]
    fn index(&self, i: usize) -> &u8 {
        &self.value[i]
    }
}

impl<T: Debug> Iterator for Holder<Vec<T>> {
    type Item = T;
    #[inline(never)]
    fn next(&mut self) -> Option<T> {
        self.value.pop()
    }
}

// ------------------------------------------------------------- non-ASCII names

pub mod ünïcödé {
    #[inline(never)]
    pub fn grüße(n: usize) -> usize {
        n + 1
    }

    pub struct Größe {
        pub wert: u64,
    }

    impl Größe {
        #[inline(never)]
        pub fn verdoppeln(&self) -> u64 {
            self.wert * 2
        }
    }
}

#[inline(never)]
pub fn 日本語(n: i64) -> i64 {
    n * 3
}

// --------------------------------------------------------------- entry point

pub fn exercise() -> usize {
    let mut total = 0usize;
    total += outer::inner::plain(1) as usize;
    total += outer::inner::deeper::buried(2) as usize;
    total += outer::inner::with_statics().len();
    total += outer::inner::Base::new(3).method(4) as usize;
    total += outer::inner::Base::new(5).consume() as usize;

    total += Holder::new(1u8).get() as usize;
    total += Holder::new(2i64).get() as usize;
    total += Holder::new(String::from("x")).get().len();
    total += Holder::new(vec![1u8, 2]).get().len();
    total += Holder::new(Some(1u32)).get().unwrap_or(0) as usize;
    let widened: u64 = Holder::new(7u32).convert();
    total += widened as usize;
    total += Pair { first: 1u8, second: "two" }.describe().len();
    total += Pair { first: vec![1u16], second: Some(2.5f32) }.describe().len();

    total += Matrix::<3, 4>::zeroed().size();
    total += Matrix::<1, 1>::zeroed().size();
    total += const_bool::<true>() as usize;
    total += const_bool::<false>() as usize;
    total += const_char::<'ß'>() as usize;
    total += const_signed::<-17>() as usize;
    total += const_wide::<340282366920938463463374607431768211455>() as usize;

    let shapes: Vec<Box<dyn Shape<Unit = f64>>> = vec![
        Box::new(Circle { radius: 1.0 }),
        Box::new(Square { side: 2.0 }),
    ];
    total += total_area(&shapes) as usize;
    total += shapes[0].name().len();
    total += shapes[0].boxed_clone().area() as usize;
    total += dyn_display(&Circle { radius: 3.0 }).len();
    total += dyn_multi(&Square { side: 1.0 }) as usize;
    total += Circle { radius: 1.0 }.convert(5u32).len();
    total += Circle { radius: 1.0 }.convert("s").len();
    total += vec![1i8, 2].convert(3i8).len();

    total += Borrowed { slot: &9u32 }.peek().len();
    total += Borrowed { slot: &"s" }.peek().len();
    total += longest("aa", "b").len();
    total += apply_hrtb(|s| &s[1..]);
    total += two_lifetimes(b"ab", b"cde");

    total += make_adder(1)(2) as usize;
    total += make_iter(9).count();
    total += fn_pointer_taker(|a, b| a + b, unsafe { std::mem::transmute::<usize, unsafe extern "C" fn(u8) -> u8>(1) }) as usize;

    total += closure_zoo(2) as usize;
    total += generic_closure(&[1u8, 2, 3]);
    total += generic_closure(&['a', 'b']);
    total += generic_closure(&[1.5f64]);

    total += iterator_chain(&[1, -2, 3]).len();
    total += fold_and_scan(&[1, 2, 3]) as usize;
    total += sort_and_dedup(vec![String::from("bb"), String::from("a")]).len();

    total += hash_map_work(&[String::from("k")]).len();
    total += btree_work(&[1, 2]).len();
    total += deque_work(2).len();
    total += shared_state(2).lock().unwrap().len();
    total += option_result(Some(" 12 ")).unwrap_or(0);
    total += hash_set_ops(&HashSet::from([1u64]), &HashSet::from([1u64, 2])).len();

    total += tuple_soup((1, (2, 3.0), [0; 4], "abcd"));
    total += unsafe { raw_pointers(std::ptr::null(), std::ptr::null_mut()) };
    total += slice_of_slices(&[&['a', 'b'][..]]);

    total += (Mode::Running(1) + Mode::Running(2) == Mode::Running(3)) as usize;
    total += Mode::default().clone().eq(&Mode::Idle) as usize;
    total += format!("{:?}", Mode::Failed { code: 1, message: String::from("e") }).len();
    let mut h = Holder { value: vec![1u8, 2, 3] };
    total += h[0] as usize;
    total += h.next().unwrap_or(0) as usize;

    total += ünïcödé::grüße(1);
    total += ünïcödé::Größe { wert: 2 }.verdoppeln() as usize;
    total += 日本語(3) as usize;

    total
}

fn main() {
    println!("{}", exercise());
}
