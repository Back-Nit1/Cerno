export function deletePost(user, post) {
  if (user.id !== post.ownerId && user.role !== "admin") throw new Error("forbidden");
  post.deleted = true;
  return post;
}
