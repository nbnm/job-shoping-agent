// name, brand, category, price, purchased_on, color, size, material, store, wears
const ROWS = [
  ["Wool overcoat","COS","Outerwear",245,"2025-01-12","#3b3f46","M","90% wool, 10% cashmere","COS",18],
  ["Heavyweight tee","Uniqlo","Tops",29,"2025-02-03","#e9e6df","M","100% cotton","Uniqlo",41],
  ["Straight denim","Levi's","Bottoms",110,"2025-02-20","#2a3a56","32/32","Cotton denim","Levi's",55],
  ["Merino crewneck","Uniqlo","Knitwear",59,"2025-03-08","#7a8b7c","M","100% merino wool","Uniqlo",22],
  ["White sneakers","Common Projects","Shoes",420,"2025-04-15","#f2f2f0","EU 43","Italian leather","SSENSE",38],
  ["Linen shirt","Arket","Tops",89,"2025-05-22","#c9d3dc","M","100% linen","Arket",9],
  ["Chinos","Dockers","Bottoms",70,"2025-06-02","#b8a98c","32/32","Cotton twill","Macy's",14],
  ["Oxford shirt","Brooks Brothers","Tops",120,"2025-06-18","#a9c1de","M","Cotton oxford","Brooks Brothers",11],
  ["Rain shell","Arc'teryx","Outerwear",380,"2025-09-10","#1d2b3a","M","Gore-Tex","Arc'teryx",7],
  ["Black tee","Uniqlo","Tops",25,"2025-09-28","#17181a","M","100% cotton","Uniqlo",30],
  ["Cashmere cardigan","Everlane","Knitwear",148,"2025-10-30","#8a6f5a","M","100% cashmere","Everlane",6],
  ["Leather belt","Anson","Accessories",65,"2025-12-06","#4a3326","34","Full-grain leather","Anson",24],
  ["Pleated trousers","COS","Bottoms",135,"2026-01-14","#44474d","32","Wool blend","COS",5],
  ["Chelsea boots","Blundstone","Shoes",210,"2026-02-11","#5b3a29","EU 43","Leather","Blundstone",12],
  ["Merino long sleeve","Icebreaker","Tops",110,"2026-03-19","#6d7f95","M","100% merino wool","Icebreaker",8],
  ["Suede loafers","G.H. Bass","Shoes",230,"2026-05-07","#8c6a4a","EU 43","Suede","G.H. Bass",3],
  ["Lightweight bomber","Aimé Leon Dore","Outerwear",340,"2026-06-21","#566149","M","Nylon","Aimé Leon Dore",2],
  ["Linen tee","Arket","Tops",45,"2026-07-30","#d9d2c2","M","100% linen","Arket",1],
  ["Tapered denim","A.P.C.","Bottoms",230,"2026-09-12","#1c2430","31","Japanese selvedge denim","A.P.C.",0],
  ["Roll-neck sweater","COS","Knitwear",125,"2026-10-24","#b9b2a6","M","Wool blend","COS",0],
  ["Minimal watch","Skagen","Accessories",160,"2026-11-02","#9aa0a6","40mm","Steel and leather","Skagen",4],
];

// a few pieces that did not work out, so the timeline has something to judge
const STATUS = {
  2: ["disposed", "Donated. The sleeves went feral."],
  7: ["disliked", "Fits like a sack."],
  9: ["unavailable", "Left on a train. Allegedly."],
  11: ["disposed", "Shrunk in the wash. Now a very expensive doll sweater."],
};

// seed version 2: the 2021-2024 history, so the 5-year timeline has something on it.
// name, brand, category, price, purchased_on, color, size, material, store, wears, status, status_note
const ROWS_V2 = [
  ["Puffer vest","Uniqlo","Outerwear",79,"2021-11-26","#2d3a2f","M","Recycled nylon","Uniqlo",34,"disposed","Donated. Vests were a phase."],
  ["Flannel shirt","L.L.Bean","Tops",49,"2021-11-26","#8a3b2f","M","Cotton flannel","L.L.Bean",20,"disposed","Elbows have retired."],
  ["Wool beanie","Carhartt","Accessories",22,"2021-11-26","#3b3f46","One size","Wool","Carhartt",18,"unavailable","Lost on a ski lift."],
  ["Denim jacket","Levi's","Outerwear",98,"2022-03-12","#4a6a8f","M","Denim","Levi's",15,"disliked","Turns out I'm not a denim-jacket person."],
  ["White tee 3-pack","Hanes","Tops",28,"2022-04-02","#f4f4f2","M","Cotton","Amazon",40,"disposed","Gone grey. All three."],
  ["Running shoes","New Balance","Shoes",120,"2022-05-20","#8c9aa5","EU 43","Mesh","New Balance",60,"disposed","Mileage: too many."],
  ["Linen trousers","Arket","Bottoms",95,"2022-06-18","#c9bfa8","32","Linen","Arket",9,"disliked","Wrinkles like a promise."],
  ["Polo shirt","Lacoste","Tops",110,"2022-07-09","#1f3a5f","M","Cotton piqué","Lacoste",14,"active",""],
  ["Aviator sunglasses","Ray-Ban","Accessories",165,"2022-08-14","#c9a227","One size","Metal","Ray-Ban",45,"unavailable","Sat on them."],
  ["Hiking boots","Salomon","Shoes",190,"2022-09-24","#5c4a3a","EU 43","Gore-Tex","REI",22,"disposed","Resoled twice. Retired with honours."],
  ["Fleece pullover","Patagonia","Knitwear",139,"2022-10-15","#5a6b4a","M","Recycled polyester","Patagonia",31,"disposed","Donated after 31 wears. Worth it."],
  ["Cable-knit sweater","J.Crew","Knitwear",89,"2022-11-25","#d8d2c4","M","Lambswool","J.Crew",16,"disliked","Beautiful. Itchy."],
  ["Black slim jeans","Levi's","Bottoms",59,"2022-11-25","#1b1b1d","32/32","Denim","Levi's",38,"disposed","Faded to grey."],
  ["Leather gloves","Dents","Accessories",45,"2022-11-25","#3a2a20","M","Lined leather","Dents",11,"unavailable","Lost one. Then the other."],
  ["Navy blazer","Suitsupply","Outerwear",349,"2023-04-29","#1f2a44","50","Wool","Suitsupply",7,"disposed","Sold. The weddings are over."],
  ["White dress shirt","Suitsupply","Tops",89,"2023-04-29","#f2f2f0","15.5","Cotton","Suitsupply",6,"disposed","Yellowed at the collar."],
  ["Leather sandals","Birkenstock","Shoes",110,"2023-06-03","#8a6a45","EU 43","Leather and cork","Birkenstock",28,"disposed","The cork gave up."],
  ["Camp collar shirt","Everlane","Tops",52,"2023-07-15","#5b8f8a","M","Viscose","Everlane",8,"disposed","Gave it to my brother."],
  ["Canvas sneakers","Converse","Shoes",65,"2023-09-09","#222222","EU 43","Canvas","Converse",33,"disposed","Soles gave up."],
  ["Down puffer jacket","The North Face","Outerwear",280,"2023-11-24","#1a1a1a","M","Down","The North Face",29,"unavailable","Zipper died. Repair not forthcoming."],
  ["Thermal base layer","Uniqlo","Tops",45,"2023-11-24","#2b2d33","M","Heattech","Uniqlo",12,"disposed","Pilled. Surrendered."],
  ["Wool socks 3-pack","Darn Tough","Accessories",24,"2023-11-24","#8a8f96","L","Merino wool","Darn Tough",36,"disposed","Holes. Many holes."],
  ["Zip hoodie","Champion","Knitwear",70,"2024-02-17","#5a5f6a","L","Cotton fleece","Champion",44,"disposed","Now only worn by the dog's bed."],
  ["Trench coat","Everlane","Outerwear",220,"2024-03-23","#b79f7a","M","Cotton","Everlane",6,"disliked","I look like a detective. Not in a fun way."],
  ["Trail runners","Hoka","Shoes",155,"2024-06-22","#4a6a7a","EU 43","Mesh","Hoka",40,"disposed","Worn into the ground. Honourably."],
  ["Corduroy overshirt","Norse Projects","Tops",160,"2024-10-12","#7a4f3a","M","Cotton cord","Norse Projects",17,"disliked","Itches. It's cotton. Somehow."],
  ["Waxed cotton jacket","Barbour","Outerwear",189,"2024-11-29","#4a4630","M","Waxed cotton","Barbour",13,"unavailable","Lent to a friend. Gone."],
  ["Cashmere scarf","Uniqlo","Accessories",59,"2024-11-29","#6b2f3a","One size","Cashmere blend","Uniqlo",9,"unavailable","Left in a taxi in Lisbon."],
];

// `since` is the seed version a row first appeared in; the server inserts newer rows into existing databases once.
const make = (r, i, since) => ({
  name: r[0], brand: r[1], category: r[2], price: r[3], purchased_on: r[4], color: r[5], size: r[6], material: r[7], store: r[8], wears: r[9],
  status: since === 1 ? (STATUS[i + 1]?.[0] ?? "active") : r[10],
  status_note: since === 1 ? (STATUS[i + 1]?.[1] ?? "") : r[11],
  notes: "", photo: `assets/items/${i + 1}.jpg`, since,
});

export const SEED_VERSION = 2;
export const SEED = [...ROWS.map((r, i) => make(r, i, 1)), ...ROWS_V2.map((r, i) => make(r, ROWS.length + i, 2))];
