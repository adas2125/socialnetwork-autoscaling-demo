function worked(result) {
  if (result.ok !== 1) throw new Error(JSON.stringify(result));
}
if (db.adminCommand({hello: 1}).msg !== "isdbgrid") throw new Error("Connect through mongos");
const shards = db.adminCommand({listShards: 1});
worked(shards);
if (shards.shards.length !== 1 || shards.shards[0].draining) {
  throw new Error("Expected one registered, usable shard");
}
const hotel = db.getSiblingDB("reservation-db");
const config = db.getSiblingDB("config");
const collections = [];
for (const name of ["reservation", "number"]) {
  if (hotel.getCollectionInfos({name: name}).length === 0) worked(hotel.createCollection(name));
  hotel.getCollection(name).createIndex({hotelId: "hashed"});
  const namespace = "reservation-db." + name;
  let metadata = config.collections.findOne({_id: namespace, dropped: {$ne: true}});
  if (!metadata || metadata.unsplittable) {
    worked(db.adminCommand({shardCollection: namespace, key: {hotelId: "hashed"}}));
  }
  metadata = config.collections.findOne({_id: namespace, dropped: {$ne: true}});
  if (!metadata || metadata.unsplittable || JSON.stringify(metadata.key) !== '{"hotelId":"hashed"}') {
    throw new Error("Unexpected sharding metadata for " + namespace);
  }
  collections.push({namespace: namespace, key: metadata.key});
}
hotel.reservation.createIndex({hotelId: 1, inDate: 1, outDate: 1});
print(EJSON.stringify({collections: collections, shards: shards}, null, 2));
