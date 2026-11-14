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

export const SEED = ROWS.map(([name, brand, category, price, purchased_on, color, size, material, store, wears], i) => ({
  name, brand, category, price, purchased_on, color, size, material, store, wears,
  status: STATUS[i + 1]?.[0] ?? "active", status_note: STATUS[i + 1]?.[1] ?? "",
  notes: "", photo: `assets/items/${i + 1}.jpg`,
}));
