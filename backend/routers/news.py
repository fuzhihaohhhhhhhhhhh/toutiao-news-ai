from fastapi import APIRouter, HTTPException
from fastapi.params import Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from config.db_config import get_db
from crud import news
from crud import news_cache
#创建 APIRouter 实例 prefix 路径前缀 tags 标签分类
router = APIRouter(prefix="/api/news",tags=["news"])

@router.get("/categories")
async def get_categories(skip: int = 0, limit: int = 100,db: AsyncSession = Depends(get_db)):
    # 从数据库获取新闻分类数据->先定义模型类->封装查询数据的方法
    categories = await news_cache.get_categories(db,skip,limit)
    return {
        "code": 200,
        "message":"获取新闻分类成功",
        "data": categories,
    }

@router.get("/list")
async def get_news_list(
        category_id: int = Query(...,alias="categoryId"),
        page: int = 1,
        page_size: int = Query(10,alias="pageSize",le=100),
        db: AsyncSession = Depends(get_db)
):
    # 思路：处理分页规则->查询新闻列表->计算总量->计算是否有更多数据
    offset = (page-1)*page_size
    news_list = await news_cache.get_news_list(db,category_id,offset,page_size)
    total = await news_cache.get_news_total(db,category_id)
    if(offset + len(news_list) < total):
        hasMore = True
    else:
        hasMore = False
    return {
        "code": 200,
        "message":"获取新闻列表成功",
        "data": {
            "list": news_list,
            "total": total,
            "hasMore": hasMore
        }
    }

@router.get("/detail")
async def get_news_detail(
        news_id: int = Query(...,alias="id"),
        db: AsyncSession = Depends(get_db)
):
    # 思路：查询新闻详情->浏览量+1->相关新闻->返回新闻详情
    news_detail = await news_cache.get_news_detail(db,news_id)
    if not news_detail:
        raise HTTPException(
            status_code=404,
            detail="新闻不存在",
        )
    # 浏览量+1
    views_res = await news.update_news_views(db,news_detail.id)
    if not views_res:
        raise HTTPException(
            status_code=404,
            detail="新闻不存在",
        )
    # 相关新闻
    related_news = await news_cache.get_related_news(db,news_id,news_detail.category_id)
    return {
        "code": 200,
        "message":"获取新闻详情成功",
        "data":{
            "id":news_detail.id,
            "title":news_detail.title,
            "content":news_detail.content,
            "image":news_detail.image,
            "author":news_detail.author,
            "publishTime":news_detail.publish_time,
            "categoryId":news_detail.category_id,
            "views":news_detail.views,
            "relatedNews":related_news
        }
    }




